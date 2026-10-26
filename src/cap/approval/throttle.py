"""How many wrong sign-in secrets the app tolerates.

Per device (client IP): PER_CLIENT failures within CLIENT_WINDOW_S lock that device
out until the window has passed. For everyone together: GLOBAL failures within
GLOBAL_WINDOW_S pause all sign-in for GLOBAL_COOLDOWN_S, so a guesser who keeps
changing address still gets about 20 tries per 25 minutes. The pause is timed, not
until restart (re-verification, 22 Oct: a permanent freeze let anyone on the Wi-Fi
lock the owner out); with 12-character secrets the timed limit already makes guessing
hopeless. People already signed in are not affected.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable

PER_CLIENT = 5
CLIENT_WINDOW_S = 15 * 60
GLOBAL = 20
GLOBAL_WINDOW_S = 10 * 60
GLOBAL_COOLDOWN_S = 15 * 60

FROZEN = "Sign-in is paused for 15 minutes after too many wrong codes. Try again after that."
LOCKED = "Too many wrong codes from this device. Wait 15 minutes and try again."


class SignInThrottle:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._by_client: dict[str, deque[float]] = {}
        self._all: deque[float] = deque()
        self._frozen_until = 0.0

    @property
    def frozen(self) -> bool:
        return self._clock() < self._frozen_until

    @staticmethod
    def _trim(times: deque[float], since: float) -> None:
        while times and times[0] <= since:
            times.popleft()

    def refusal(self, client: str) -> str | None:
        """Why this device may not try now, or None when it may."""
        with self._lock:
            if self._clock() < self._frozen_until:
                return FROZEN
            times = self._by_client.get(client)
            if times is not None:
                self._trim(times, self._clock() - CLIENT_WINDOW_S)
                if len(times) >= PER_CLIENT:
                    return LOCKED
            return None

    def failed(self, client: str) -> None:
        with self._lock:
            now = self._clock()
            self._by_client.setdefault(client, deque()).append(now)
            self._all.append(now)
            self._trim(self._all, now - GLOBAL_WINDOW_S)
            if len(self._all) >= GLOBAL:
                self._frozen_until = now + GLOBAL_COOLDOWN_S
                self._all.clear()

    def succeeded(self, client: str) -> None:
        with self._lock:
            self._by_client.pop(client, None)
