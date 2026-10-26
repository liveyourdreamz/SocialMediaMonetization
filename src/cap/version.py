"""Which build is running: the git commit and its date, shown on the Operator screen
and printed by `cap serve`, so "is this the new code?" has an answer on the page."""

from __future__ import annotations

import json
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


@lru_cache(maxsize=1)
def pack_version() -> str:
    """The prompt pack's version, stamped on every post record. It lives here, at the
    bottom layer, so the record schema can stamp it without importing the prompts."""
    index = Path(__file__).resolve().parent / "prompts" / "pack" / "index.json"
    return str(json.loads(index.read_text(encoding="utf-8"))["pack_version"])
