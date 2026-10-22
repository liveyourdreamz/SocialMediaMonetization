"""Reaching the laptop's app from a phone on the same Wi-Fi.

`cap serve --host 0.0.0.0` used to print "http://<this laptop's address>:8000",
leaving Hemant to find the address. These helpers list the laptop's local-network
IPv4 addresses as full URLs, and the one Windows firewall command for when the phone
still cannot connect.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterable, Mapping

FIREWALL_RULE = "Content Agent Portfolio"
_PROBE = ("192.0.2.1", 80)  # TEST-NET-1: connecting a UDP socket sends nothing


def _is_lan(addr: str) -> bool:
    try:
        ip = ipaddress.IPv4Address(addr)
    except ValueError:
        return False
    return ip.is_private and not ip.is_loopback and not ip.is_link_local


def lan_ipv4(candidates: Iterable[str] | None = None) -> list[str]:
    """Local-network IPv4 addresses, the one the default route uses first."""
    if candidates is None:
        found: list[str] = []
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(_PROBE)
                found.append(s.getsockname()[0])
        except OSError:
            pass
        try:
            infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
            found += [str(info[4][0]) for info in infos]
        except OSError:
            pass
        candidates = found
    return list(dict.fromkeys(a for a in candidates if _is_lan(a)))


def phone_urls(port: int, addresses: Iterable[str] | None = None) -> list[str]:
    return [f"http://{a}:{port}/login" for a in lan_ipv4(addresses)]


def firewall_command(port: int) -> str:
    """Allow the port on Private networks (run once, in a terminal opened as administrator)."""
    return (
        f'netsh advfirewall firewall add rule name="{FIREWALL_RULE} {port}" '
        f"dir=in action=allow protocol=TCP localport={port} profile=private"
    )


def boxed(lines: Iterable[str]) -> str:
    body = list(lines)
    width = max((len(s) for s in body), default=0)
    edge = "+" + "-" * (width + 2) + "+"
    return "\n".join([edge, *(f"| {s.ljust(width)} |" for s in body), edge])


def phone_block(
    port: int, secrets: Mapping[str, str], addresses: Iterable[str] | None = None
) -> str:
    """The boxed block `cap serve` prints: where to point the phone and how to sign in."""
    urls = phone_urls(port, addresses)
    lines = ["ON YOUR PHONE (same Wi-Fi as this laptop), open:"]
    lines += [f"  {u}" for u in urls] or ["  (no Wi-Fi address found: is the laptop on Wi-Fi?)"]
    if secrets:
        lines += ["", "Sign in with:"]
        lines += [f"  {who}: {secret}" for who, secret in secrets.items()]
    lines += [
        "",
        "Phone cannot connect? Allow the app through the Windows firewall once:",
        "open a terminal as administrator and run",
    ]
    return boxed(lines) + "\n  " + firewall_command(port)
