"""H9 (22 Oct review): the source fetcher reaches public addresses only, and the
address it connects to is the one it checked (no DNS rebinding)."""

from __future__ import annotations

import http.server
import itertools
import socket
import threading

import httpx
import pytest

from cap import fetch

PUBLIC = "93.184.215.14"


def _dns(monkeypatch, answers):
    """Fake resolver: `answers(host)` -> list of IPs, or None for the real one."""
    real = socket.getaddrinfo

    def fake(host, port, *a, **k):
        ips = answers(host)
        if ips is None:
            return real(host, port, *a, **k)
        fam = lambda ip: socket.AF_INET6 if ":" in ip else socket.AF_INET  # noqa: E731
        return [(fam(ip), socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]

    monkeypatch.setattr(socket, "getaddrinfo", fake)


@pytest.fixture
def internal_server():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"INTERNAL-ONLY-SECRET")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_port
    srv.shutdown()


def test_h9_dns_rebinding_never_reaches_the_second_answer(monkeypatch, internal_server):
    """The exploit: public for the check, 127.0.0.1 for the connect (TTL 0)."""
    n = itertools.count()
    _dns(
        monkeypatch,
        lambda h: (
            ([PUBLIC] if next(n) % 2 == 0 else ["127.0.0.1"])
            if h == "rebind.attacker.example"
            else None
        ),
    )
    doc = fetch.fetch_sources([f"http://rebind.attacker.example:{internal_server}/"])[0]
    assert "INTERNAL-ONLY-SECRET" not in doc["body"]
    assert fetch.failed_reason(doc).startswith("refused:")


def test_h9_the_connection_is_refused_even_when_the_pre_check_is_skipped(
    monkeypatch, internal_server
):
    """The transport is the guard, not the pre-check: every hop is vetted at connect."""
    _dns(monkeypatch, lambda h: ["127.0.0.1"] if h == "inside.example" else None)
    monkeypatch.setattr(fetch, "check_public", lambda url: None)
    doc = fetch.fetch_sources([f"http://inside.example:{internal_server}/"])[0]
    assert "INTERNAL-ONLY-SECRET" not in doc["body"]
    assert fetch.failed_reason(doc).startswith("refused:")


def test_w2_every_dns_answer_is_checked_not_only_the_first(monkeypatch):
    _dns(monkeypatch, lambda h: [PUBLIC, "10.0.0.5"] if h == "mixed.example" else None)
    with pytest.raises(fetch.BlockedURLError):
        fetch.check_public("https://mixed.example/")
    doc = fetch.fetch_sources(["https://mixed.example/"])[0]
    assert fetch.failed_reason(doc).startswith("refused:")


@pytest.mark.parametrize(
    "ip",
    [
        "100.64.0.1",
        "100.127.255.254",
        "127.0.0.1",
        "169.254.169.254",
        "10.1.2.3",
        "0.0.0.0",
        "::1",
        "::ffff:127.0.0.1",
        "fd00::1",
        "224.0.0.1",
        "192.0.2.10",
    ],
)
def test_h9_only_globally_routable_addresses_pass(monkeypatch, ip):
    _dns(monkeypatch, lambda h: [ip] if h == "x.example" else None)
    with pytest.raises(fetch.BlockedURLError):
        fetch.check_public("http://x.example/")
    host = f"[{ip}]" if ":" in ip else ip
    doc = fetch.fetch_sources([f"http://{host}/"])[0]
    assert fetch.failed_reason(doc).startswith("refused:")


def test_h9_the_vetted_address_is_dialled_with_the_original_host_and_sni(monkeypatch):
    _dns(monkeypatch, lambda h: [PUBLIC] if h == "news.example" else None)
    seen_parts: list[tuple[str, str, dict]] = []

    def dial(self, request):
        seen_parts.append((str(request.url), request.headers["host"], dict(request.extensions)))
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "/new"}, request=request)
        return httpx.Response(
            200, headers={"content-type": "text/plain"}, text="the article", request=request
        )

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", dial)
    doc = fetch.fetch_sources(["https://news.example/old"])[0]
    assert doc["body"] == "the article"
    (u1, h1, x1), (u2, h2, x2) = seen_parts
    assert u1 == f"https://{PUBLIC}/old" and h1 == "news.example"
    assert x1["sni_hostname"] == "news.example"
    # the relative redirect resolved against the name, and was vetted and pinned again
    assert u2 == f"https://{PUBLIC}/new" and h2 == "news.example"
