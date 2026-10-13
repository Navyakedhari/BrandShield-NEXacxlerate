"""SSRF-safe URL reachability checking.

Rules: http/https only, standard ports only, no credentials in the URL, every hostname is resolved and
*all* addresses must be public (global) unicast. Redirects are followed manually (limited) and every hop
is re-validated. The connection is pinned to the validated IP (Host header + SNI keep the hostname) so a
DNS answer cannot change between validation and connect. Response bodies are capped.
"""
import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

Resolver = Callable[[str], list[str]]
ClientFactory = Callable[[], httpx.Client]

_ALLOWED_PORTS = {80, 443}


class UnsafeURLError(Exception):
    """The URL points somewhere we refuse to connect to."""


@dataclass(frozen=True)
class CheckResult:
    status: str  # verified | unreachable
    message: str
    http_status: int | None = None

    @property
    def ok(self) -> bool:
        return self.status == "verified"


def system_resolver(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"Could not resolve host {host}") from exc
    return sorted({info[4][0] for info in infos})


def is_public_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return (
        addr.is_global
        and not addr.is_multicast
        and not addr.is_loopback
        and not addr.is_link_local
        and not addr.is_private
        and not addr.is_reserved
        and not addr.is_unspecified
    )


def validate_public_url(url: str, resolver: Resolver = system_resolver) -> tuple[str, str]:
    """Return (host, pinned_ip) for a safe URL, else raise UnsafeURLError."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise UnsafeURLError("Only http and https URLs are allowed")
    if parts.username or parts.password:
        raise UnsafeURLError("URLs with credentials are not allowed")
    host = parts.hostname
    if not host:
        raise UnsafeURLError("URL has no host")
    try:
        port = parts.port
    except ValueError as exc:
        raise UnsafeURLError("Invalid port") from exc
    if port is not None and port not in _ALLOWED_PORTS:
        raise UnsafeURLError("Only ports 80 and 443 are allowed")
    host = host.rstrip(".").lower()
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise UnsafeURLError("Host is not public")
    try:
        ipaddress.ip_address(host)
        addresses = [host]  # IP literal: still must be public
    except ValueError:
        addresses = resolver(host)
    if not addresses:
        raise UnsafeURLError(f"Could not resolve host {host}")
    bad = [a for a in addresses if not is_public_ip(a)]
    if bad:
        raise UnsafeURLError("Host resolves to a non-public address")
    return host, addresses[0]


def _default_client() -> httpx.Client:
    return httpx.Client(follow_redirects=False, trust_env=False)


class UrlChecker:
    """Injectable reachability checker; tests pass a fake resolver and an httpx MockTransport client."""

    def __init__(
        self,
        *,
        resolver: Resolver = system_resolver,
        client_factory: ClientFactory = _default_client,
        timeout: float = 5.0,
        max_redirects: int = 3,
        max_bytes: int = 65536,
    ) -> None:
        self.resolver = resolver
        self.client_factory = client_factory
        self.timeout = timeout
        self.max_redirects = max_redirects
        self.max_bytes = max_bytes

    def check(self, url: str) -> CheckResult:
        """Never raises: every failure becomes an 'unreachable' result with a message."""
        try:
            return self._check(url if "://" in url else f"https://{url}")
        except UnsafeURLError as exc:
            return CheckResult("unreachable", f"URL not checked: {exc}")
        except httpx.TimeoutException:
            return CheckResult("unreachable", "The URL timed out")
        except httpx.HTTPError:
            return CheckResult("unreachable", "The URL could not be reached")
        except Exception:  # noqa: BLE001 - a failed check must never crash the request
            return CheckResult("unreachable", "The URL could not be checked")

    def _check(self, url: str) -> CheckResult:
        with self.client_factory() as client:
            for _hop in range(self.max_redirects + 1):
                host, ip = validate_public_url(url, self.resolver)
                parts = urlsplit(url)
                netloc_ip = f"[{ip}]" if ":" in ip else ip
                if parts.port:
                    netloc_ip += f":{parts.port}"
                pinned = urlunsplit((parts.scheme, netloc_ip, parts.path or "/", parts.query, ""))
                with client.stream(
                    "GET",
                    pinned,
                    headers={"Host": parts.netloc.split("@")[-1], "User-Agent": "ScamMindAI-LinkCheck/1.0"},
                    extensions={"sni_hostname": host},
                    timeout=self.timeout,
                ) as resp:
                    read = 0
                    for chunk in resp.iter_bytes():
                        read += len(chunk)
                        if read >= self.max_bytes:
                            break  # size cap: stop reading, we only need the status
                    code = resp.status_code
                    location = resp.headers.get("location")
                if 300 <= code < 400 and location:
                    url = urljoin(url, location)
                    continue
                if 200 <= code < 300:
                    return CheckResult("verified", "URL is reachable", code)
                return CheckResult("unreachable", f"The URL answered with HTTP {code}", code)
        return CheckResult("unreachable", "Too many redirects")
