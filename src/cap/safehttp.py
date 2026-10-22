"""Outbound HTTP to addresses a brief supplied: public internet only (SSRF defence).

A name is resolved once, every address it resolves to must be globally routable
(ip.is_global: no loopback, private, link-local/metadata, carrier-grade NAT
100.64.0.0/10, reserved or multicast), and the connection then goes to the vetted
address itself. The request keeps the original name in its Host header and in TLS
(SNI and certificate check), so a name that answers "public" to the check and
"127.0.0.1" a moment later (DNS rebinding) never reaches the second answer. Every
request through the transport is vetted, so every redirect hop is too.
"""

from __future__ import annotations

import ipaddress
import socket

import httpx


class BlockedURLError(RuntimeError):
    pass


def is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def resolve_public(host: str, port: int) -> str:
    """The address to connect to for `host`; refuses when ANY answer is not public."""
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        if not is_public(literal):
            raise BlockedURLError(f"{host} is not a public address")
        return str(literal)
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise httpx.ConnectError(f"cannot resolve {host}") from e
    addresses = [ipaddress.ip_address(info[4][0]) for info in infos]
    if not addresses:
        raise httpx.ConnectError(f"cannot resolve {host}")
    if not all(is_public(ip) for ip in addresses):
        raise BlockedURLError(f"{host} is not a public address")
    return str(addresses[0])


class PinnedTransport(httpx.HTTPTransport):
    """An httpx transport that connects only to a vetted public address."""

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        if url.scheme not in ("http", "https") or not url.host:
            raise BlockedURLError("only public http(s) pages")
        port = url.port or (443 if url.scheme == "https" else 80)
        ip = resolve_public(url.host, port)
        request.url = url.copy_with(host=ip)
        if url.scheme == "https":
            request.extensions = {**request.extensions, "sni_hostname": url.host}
        # httpx set the Host header from the name when the request was built
        try:
            return super().handle_request(request)
        finally:
            request.url = url  # the response (and a relative redirect) sees the name
