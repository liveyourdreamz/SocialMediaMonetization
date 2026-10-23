"""The console encoding. Windows consoles default to cp1252; Falak's Arabic titles are
printed to them, so every entry point switches stdout and stderr to UTF-8 first."""

from __future__ import annotations

import sys


def utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure") and (stream.encoding or "").lower() != "utf-8":
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
