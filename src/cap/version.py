"""Which build is running: the git commit and its date, shown on the Operator screen
and printed by `cap serve`, so "is this the new code?" has an answer on the page."""

from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def build_id() -> str:
    root = Path(__file__).resolve().parents[2]
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "log", "-1", "--format=%h %cd", "--date=format:%d %b %H:%M"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown build (not a git checkout)"
