"""First-run setup on a laptop: the local .env and the Anthropic API key.

Lives in Python (not in the launchers) so it is the same on Windows, Mac and Linux
and is covered by tests. The key is typed hidden and written only to .env, which is
git-ignored; it is never printed, logged or sent anywhere but Anthropic.
"""

from __future__ import annotations

import getpass
import sys
from collections.abc import Callable
from pathlib import Path

KEY = "CAP_ANTHROPIC_API_KEY"
BACKEND = "CAP_MODEL_BACKEND"


def read_key(path: Path) -> str | None:
    """The key stored in .env, or None when absent, blank or commented out."""
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{KEY}="):
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            return value or None
    return None


def backend(path: Path) -> str:
    return "anthropic" if read_key(path) else "mock"


def write_key(path: Path, key: str | None) -> None:
    """Set (or clear) the key, keeping every other line of .env as it is."""
    key = (key or "").strip()
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    kept = [
        ln
        for ln in lines
        if not ln.lstrip("# ").startswith(f"{KEY}=") and not ln.startswith(f"{BACKEND}=")
    ]
    while kept and not kept[-1].strip():
        kept.pop()
    kept.append(f"{BACKEND}={'anthropic' if key else 'mock'}")
    if key:
        kept.append(f"{KEY}={key}")
    else:
        kept.append(f"# {KEY}=")
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")


def ensure_local_secrets(path: Path) -> None:
    """A random approval-signing key and a phone passcode, written once into .env. The
    public default key would let anyone on the Wi-Fi forge a sign-in cookie."""
    import secrets

    text = path.read_text(encoding="utf-8") if path.exists() else ""
    have = {ln.split("=", 1)[0] for ln in text.splitlines() if "=" in ln and not ln.startswith("#")}
    add: list[str] = []
    if "CAP_APPROVAL_SIGNING_KEY" not in have:
        add.append(f"CAP_APPROVAL_SIGNING_KEY={secrets.token_hex(32)}")
    if "CAP_LOCAL_PASSCODE" not in have:
        add.append(f"CAP_LOCAL_PASSCODE={secrets.randbelow(900000) + 100000}")
    if add:
        sep = "" if not text or text.endswith("\n") else "\n"
        path.write_text(text + sep + "\n".join(add) + "\n", encoding="utf-8")


def read_passcode(path: Path) -> str:
    for ln in path.read_text(encoding="utf-8").splitlines() if path.exists() else []:
        if ln.startswith("CAP_LOCAL_PASSCODE="):
            return ln.split("=", 1)[1].strip()
    return ""


def ensure_env(
    path: Path = Path(".env"),
    *,
    force: bool = False,
    ask: Callable[[str], str] | None = None,
    out: Callable[[str], None] = print,
) -> str:
    """Make sure .env exists and (when interactive) holds a key. Returns the backend.

    `ask` is the prompt function; None means getpass when stdin is a terminal and no
    prompt at all otherwise (a service or a test never blocks on input)."""
    if not path.exists():
        example = path.with_name(".env.example")
        path.write_text(example.read_text(encoding="utf-8") if example.exists() else "")
    ensure_local_secrets(path)
    have = read_key(path)
    if have and not force:
        out(f"API key: saved in {path.resolve()} (run `uv run cap key` to change it)")
        return "anthropic"
    if ask is None:
        if not force and not sys.stdin.isatty():
            return backend(path)  # a service or a test never blocks on input
        ask = getpass.getpass
    out("")
    out(f"Anthropic API key. Typed hidden; saved only to {path.resolve()}.")
    out("Leave blank for mock mode (sample drafts, no network).")
    key = ask("API key: ").strip()
    if key and not key.startswith("sk-"):
        out("That does not look like an Anthropic key (they start with sk-). Saved anyway.")
    write_key(path, key)
    out(
        "Saved: real drafts and judges." if key else "No key: mock mode. `uv run cap key` adds one."
    )
    return "anthropic" if key else "mock"


def check_key(key: str) -> str | None:
    """One cheap call to Anthropic. Returns an error string, or None when the key works."""
    try:
        import anthropic

        anthropic.Anthropic(api_key=key).models.list(limit=1)
    except Exception as e:  # noqa: BLE001 - any failure is the same answer to the operator
        return f"{type(e).__name__}: {str(e)[:120]}"
    return None
