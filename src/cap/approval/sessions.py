"""Sign-in sessions held by the server.

The browser's cookie carries a random session id and nothing else; the person behind
it lives here. Signing out deletes the entry, so a copied cookie stops working at
once, and a restart signs everyone out. A session lasts LIFETIME_S from sign-in.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

LIFETIME_S = 8 * 3600


@dataclass(frozen=True)
class Session:
    name: str
    expires_at: float


class SessionStore:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._by_id: dict[str, Session] = {}
        self._lock = threading.Lock()

    def start(self, name: str) -> str:
        sid = secrets.token_urlsafe(32)
        with self._lock:
            now = self._clock()
            self._by_id = {k: v for k, v in self._by_id.items() if v.expires_at > now}
            self._by_id[sid] = Session(name, now + LIFETIME_S)
        return sid

    def name_for(self, sid: str | None) -> str | None:
        """The signed-in person, or None for an unknown, revoked or expired id."""
        if not sid:
            return None
        with self._lock:
            session = self._by_id.get(sid)
            if session is None:
                return None
            if session.expires_at <= self._clock():
                del self._by_id[sid]
                return None
            return session.name

    def end(self, sid: str | None) -> None:
        if sid:
            with self._lock:
                self._by_id.pop(sid, None)
