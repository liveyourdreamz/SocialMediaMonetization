"""Which Host headers the app answers (DNS rebinding defence).

A web page the owner opens on the laptop can point its own domain at 127.0.0.1 and
then talk to this app from inside his browser. The browser still sends that domain as
the Host header, so every request whose Host is not one of this machine's own names
is refused with 400 before any route runs: localhost, 127.0.0.1, ::1, the laptop's
IPv4 addresses (how the phone reaches it on the Wi-Fi) and the host of
public_base_url. The test suite's TestClient hosts are added only under the test-only
setting.
"""

from __future__ import annotations

import ipaddress
import socket
import threading
import time
from collections.abc import Callable, Iterable
from urllib.parse import urlparse

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from cap.config import Settings

LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})
TEST_HOSTS = frozenset({"testserver", "testclient"})
LAN_RECHECK_S = 30.0  # a new Wi-Fi address is picked up within this long


def host_of(header: str) -> str:
    """The name in a Host header: no port, no IPv6 brackets, lower case, no final dot."""
    h = header.strip().lower()
    if h.startswith("["):
        h = h[1 : h.find("]")] if "]" in h else ""
    elif h.count(":") == 1:
        h = h.split(":", 1)[0]
    return h.rstrip(".")


def lan_ipv4() -> frozenset[str]:
    """This machine's IPv4 addresses, as the phone would type them."""
    found: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(str(info[4][0]))
    except OSError:
        pass
    try:  # the address of the default route; a UDP "connect" sends nothing
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            found.add(str(s.getsockname()[0]))
    except OSError:
        pass
    return frozenset(ip for ip in found if not ipaddress.ip_address(ip).is_unspecified)


def static_hosts(settings: Settings) -> frozenset[str]:
    """Everything allowed that does not depend on the current network."""
    allowed = set(LOOPBACK)
    if settings.public_base_url:
        public = urlparse(settings.public_base_url).hostname
        if public:
            allowed.add(public.lower().rstrip("."))
    if settings.test_client_on:
        allowed |= TEST_HOSTS
    return frozenset(allowed)


class HostGuard:
    """ASGI middleware: 400 for any HTTP or WebSocket request with a foreign Host."""

    def __init__(
        self,
        app: ASGIApp,
        allowed: Iterable[str],
        lan: Callable[[], frozenset[str]] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.app = app
        self.allowed = frozenset(allowed)
        self._lan_source = lan or lan_ipv4
        self._clock = clock
        self._lan: frozenset[str] = self._lan_source()
        self._lan_at = clock()
        self._lock = threading.Lock()

    def _is_lan(self, host: str) -> bool:
        if host in self._lan:
            return True
        try:
            if ipaddress.ip_address(host).version != 4:
                return False
        except ValueError:
            return False  # a name: only the fixed list may carry names
        with self._lock:
            if self._clock() - self._lan_at >= LAN_RECHECK_S:
                self._lan, self._lan_at = self._lan_source(), self._clock()
            return host in self._lan

    def allows(self, header: str) -> bool:
        host = host_of(header)
        return bool(host) and (host in self.allowed or self._is_lan(host))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            header = ""
            for k, v in scope.get("headers", []):
                if k == b"host":
                    header = v.decode("latin-1")
                    break
            if not self.allows(header):
                resp = PlainTextResponse("Invalid host header", status_code=400)
                await resp(scope, receive, send)
                return
        await self.app(scope, receive, send)
