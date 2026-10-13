"""SSRF-safe download of a candidate's logo / icon from a URL found in a search result.

Same rules as `url_safety` (public hosts only, standard ports, no credentials, every redirect hop re-validated,
connection pinned to the validated IP) plus: a hard size cap while streaming, a content-type allow-list, a
magic-byte check, and a decompression-bomb guard. Never raises anything but `FetchError`; callers treat any
failure as "logo unavailable" and carry on (a failed logo fetch never fails a candidate or a scan).
"""

from __future__ import annotations

import io
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from PIL import Image

from app.core.pipeline.logo import sniff_format
from app.core.pipeline.url_safety import ClientFactory, Resolver, UnsafeURLError, system_resolver, validate_public_url

ALLOWED_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif")
MAX_PIXELS = 25_000_000


class FetchError(Exception):
    """The image could not be fetched or is not an acceptable image. The message is safe to show."""


def _default_client() -> httpx.Client:
    return httpx.Client(follow_redirects=False, trust_env=False)


class SafeFetcher:
    def __init__(
        self,
        *,
        resolver: Resolver = system_resolver,
        client_factory: ClientFactory = _default_client,
        timeout: float = 5.0,
        max_redirects: int = 3,
        max_bytes: int = 2 * 1024 * 1024,
    ) -> None:
        self.resolver = resolver
        self.client_factory = client_factory
        self.timeout = timeout
        self.max_redirects = max_redirects
        self.max_bytes = max_bytes

    def fetch_image(self, url: str) -> Image.Image:
        data = self.fetch_bytes(url)
        Image.MAX_IMAGE_PIXELS = MAX_PIXELS
        try:
            img = Image.open(io.BytesIO(data))
            img.load()
            return img
        except Exception as exc:  # noqa: BLE001 - corrupt image, bomb guard, truncated file ...
            raise FetchError("The file is not a valid image") from exc

    def fetch_bytes(self, url: str) -> bytes:
        try:
            return self._fetch(url)
        except UnsafeURLError as exc:
            raise FetchError(f"Refused: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise FetchError("Timed out") from exc
        except httpx.HTTPError as exc:
            raise FetchError("Could not be reached") from exc

    def _fetch(self, url: str) -> bytes:
        with self.client_factory() as client:
            for _hop in range(self.max_redirects + 1):
                host, ip = validate_public_url(url, self.resolver)  # raises UnsafeURLError for private / bad hosts
                parts = urlsplit(url)
                netloc_ip = f"[{ip}]" if ":" in ip else ip
                if parts.port:
                    netloc_ip += f":{parts.port}"
                pinned = urlunsplit((parts.scheme, netloc_ip, parts.path or "/", parts.query, ""))
                with client.stream(
                    "GET",
                    pinned,
                    headers={"Host": parts.netloc.split("@")[-1], "User-Agent": "ScamMindAI-ImageFetch/1.0", "Accept": "image/*"},
                    extensions={"sni_hostname": host},
                    timeout=self.timeout,
                ) as resp:
                    code = resp.status_code
                    location = resp.headers.get("location")
                    if 300 <= code < 400 and location:
                        url = urljoin(url, location)
                        continue
                    if code != 200:
                        raise FetchError(f"The server answered with HTTP {code}")
                    ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                    if ctype not in ALLOWED_TYPES:
                        raise FetchError("Not an image (content type is not png, jpeg, webp or gif)")
                    declared = resp.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > self.max_bytes:
                        raise FetchError("Image is too large")
                    buf = bytearray()
                    for chunk in resp.iter_bytes():
                        buf.extend(chunk)
                        if len(buf) > self.max_bytes:
                            raise FetchError("Image is too large")
                    data = bytes(buf)
                if not data or (sniff_format(data[:16]) is None and not data.startswith((b"GIF87a", b"GIF89a"))):
                    raise FetchError("The file content is not a supported image")
                return data
        raise FetchError("Too many redirects")
