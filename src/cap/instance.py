"""One app per data folder (review: double launch).

A second `cap serve` used to load the store first, which marked the first window's
running posts as interrupted, and only then fail on port 8000. Now the second launch
checks this lock and the port before touching anything and opens the browser on the
app that is already running instead.

The lock is an OS file lock (msvcrt on Windows, flock elsewhere): it goes away with
the process, so a crash never leaves a stale lock behind. The file also says which
port the app serves on, for the updater (cap.updater reads "port=N").
"""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

LOCK_FILE = "serve.lock"
_LOCK_AT = 1 << 16  # Windows locks a byte range; this one is past the readable "port=" text


class AlreadyRunningError(Exception):
    def __init__(self, port: int) -> None:
        super().__init__(f"the app is already running on port {port}")
        self.port = port


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Something already answers on the port (an app from another window, or another program)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def _recorded_port(path: Path, default: int) -> int:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("port="):
                return int(line.split("=", 1)[1])
    except (OSError, ValueError):
        pass
    return default


class InstanceLock:
    """Held for as long as `cap serve` runs. `acquire` raises AlreadyRunningError."""

    def __init__(self, data_dir: Path, port: int) -> None:
        self.path = data_dir / LOCK_FILE
        self.port = port
        self._fd: int | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o644)
        try:
            _lock(fd)
        except OSError:
            os.close(fd)
            raise AlreadyRunningError(_recorded_port(self.path, self.port)) from None
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, f"port={self.port}\npid={os.getpid()}\n".encode())
        self._fd = fd

    def release(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            _unlock(fd)
        finally:
            os.close(fd)

    def __enter__(self) -> InstanceLock:
        self.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


if sys.platform == "win32":
    import msvcrt

    def _lock(fd: int) -> None:
        os.lseek(fd, _LOCK_AT, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd: int) -> None:
        os.lseek(fd, _LOCK_AT, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)
