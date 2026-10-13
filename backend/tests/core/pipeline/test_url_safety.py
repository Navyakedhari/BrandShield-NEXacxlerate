import httpx
import pytest

from app.core.pipeline.url_safety import UnsafeURLError, UrlChecker, is_public_ip, validate_public_url

PUBLIC = "93.184.216.34"


def resolver_for(mapping: dict[str, list[str]]):
    def resolve(host: str) -> list[str]:
        if host not in mapping:
            raise UnsafeURLError(f"Could not resolve host {host}")
        return mapping[host]

    return resolve


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1", "127.1.2.3", "10.0.0.1", "172.16.5.4", "172.31.255.255", "192.168.1.1",
        "169.254.169.254",  # cloud metadata
        "169.254.0.1", "0.0.0.0", "100.64.0.1", "192.0.0.1", "198.18.0.1", "224.0.0.1", "240.0.0.1",
        "255.255.255.255", "::1", "::", "fe80::1", "fc00::1", "fd00:ec2::254", "::ffff:127.0.0.1",
        "::ffff:10.0.0.1", "::ffff:169.254.169.254", "ff02::1", "not-an-ip",
    ],
)
def test_non_public_ips_rejected(ip):
    assert is_public_ip(ip) is False


@pytest.mark.parametrize("ip", [PUBLIC, "8.8.8.8", "1.1.1.1", "2606:4700:4700::1111", "172.32.0.1"])
def test_public_ips_accepted(ip):
    assert is_public_ip(ip) is True


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/x",
        "file:///etc/passwd",
        "gopher://example.com",
        "javascript:alert(1)",
        "http://localhost/",
        "http://LOCALHOST./",
        "http://foo.localhost/",
        "http://printer.local/",
        "http://metadata.internal/",
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.1.2.3/",
        "http://user:pass@example.com/",
        "http://example.com:22/",
        "http://example.com:8080/",
        "http:///nohost",
        "http://2130706433/",  # decimal-encoded 127.0.0.1 parses as a name and resolves to private in the fake DNS
    ],
)
def test_unsafe_urls_rejected(url):
    resolver = resolver_for({"2130706433": ["127.0.0.1"]})
    with pytest.raises(UnsafeURLError):
        validate_public_url(url, resolver)


def test_hostname_resolving_to_private_rejected():
    with pytest.raises(UnsafeURLError):
        validate_public_url("https://evil.example/", resolver_for({"evil.example": ["10.0.0.5"]}))


def test_any_private_address_among_many_rejects():
    r = resolver_for({"mixed.example": [PUBLIC, "192.168.0.10"]})
    with pytest.raises(UnsafeURLError):
        validate_public_url("https://mixed.example/", r)


def test_dns_failure_is_unsafe_not_crash():
    with pytest.raises(UnsafeURLError):
        validate_public_url("https://nx.example/", resolver_for({}))


def test_public_host_ok_and_pinned():
    host, ip = validate_public_url("https://good.example/path", resolver_for({"good.example": [PUBLIC]}))
    assert (host, ip) == ("good.example", PUBLIC)


def make_checker(handler, mapping, **kw):
    return UrlChecker(
        resolver=resolver_for(mapping),
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False),
        **kw,
    )


def test_checker_verified_and_connects_to_pinned_ip_with_host_header():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.url.host, req.headers["host"], req.extensions.get("sni_hostname")))
        return httpx.Response(200, text="ok")

    res = make_checker(handler, {"instagram.com": [PUBLIC]}).check("instagram.com/acme")
    assert res.status == "verified" and res.ok
    assert seen == [(PUBLIC, "instagram.com", "instagram.com")]


def test_checker_404_is_unreachable():
    res = make_checker(lambda r: httpx.Response(404), {"x.com": [PUBLIC]}).check("x.com/nobody")
    assert res.status == "unreachable"
    assert "404" in res.message


def test_checker_follows_redirect_and_revalidates_each_hop():
    hops = []

    def handler(req: httpx.Request) -> httpx.Response:
        hops.append(req.headers["host"])
        if req.headers["host"] == "a.example":
            return httpx.Response(302, headers={"location": "https://b.example/final"})
        return httpx.Response(200)

    res = make_checker(handler, {"a.example": [PUBLIC], "b.example": ["8.8.8.8"]}).check("https://a.example/")
    assert res.ok
    assert hops == ["a.example", "b.example"]


def test_redirect_to_private_ip_blocked():
    def handler(req):
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})

    res = make_checker(handler, {"a.example": [PUBLIC]}).check("https://a.example/")
    assert res.status == "unreachable"
    assert "not checked" in res.message


def test_redirect_to_host_resolving_private_blocked():
    def handler(req):
        return httpx.Response(302, headers={"location": "https://internal.example/"})

    res = make_checker(handler, {"a.example": [PUBLIC], "internal.example": ["10.0.0.9"]}).check("https://a.example/")
    assert res.status == "unreachable"


def test_redirect_to_non_http_scheme_blocked():
    res = make_checker(
        lambda r: httpx.Response(302, headers={"location": "file:///etc/passwd"}), {"a.example": [PUBLIC]}
    ).check("https://a.example/")
    assert res.status == "unreachable"


def test_too_many_redirects():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(302, headers={"location": "https://a.example/again"})

    res = make_checker(handler, {"a.example": [PUBLIC]}, max_redirects=3).check("https://a.example/")
    assert res.status == "unreachable"
    assert res.message == "Too many redirects"
    assert len(calls) == 4  # first request + 3 redirects


def test_timeout_becomes_unreachable():
    def handler(req):
        raise httpx.ReadTimeout("slow", request=req)

    res = make_checker(handler, {"a.example": [PUBLIC]}).check("https://a.example/")
    assert res == res.__class__("unreachable", "The URL timed out")


def test_connection_error_becomes_unreachable():
    def handler(req):
        raise httpx.ConnectError("refused", request=req)

    assert make_checker(handler, {"a.example": [PUBLIC]}).check("https://a.example/").status == "unreachable"


def test_unexpected_exception_never_escapes():
    def handler(req):
        raise ValueError("weird")

    assert make_checker(handler, {"a.example": [PUBLIC]}).check("https://a.example/").status == "unreachable"


def test_body_size_cap_stops_reading():
    class Endless(httpx.SyncByteStream):
        def __init__(self):
            self.read = 0

        def __iter__(self):
            while True:
                self.read += 1024
                yield b"x" * 1024

    stream = Endless()
    res = make_checker(lambda r: httpx.Response(200, stream=stream), {"a.example": [PUBLIC]}, max_bytes=4096).check(
        "https://a.example/"
    )
    assert res.ok
    assert stream.read <= 8192  # stopped shortly after the cap instead of reading forever


def test_checker_blocks_ip_literal_private_url():
    res = make_checker(lambda r: httpx.Response(200), {}).check("http://127.0.0.1/")
    assert res.status == "unreachable"
