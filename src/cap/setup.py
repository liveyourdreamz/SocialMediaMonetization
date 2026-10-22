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

from cap.secretfile import restrict_to_owner

KEY = "CAP_ANTHROPIC_API_KEY"
BACKEND = "CAP_MODEL_BACKEND"


# Written in place of the key when Hemant leaves the prompt blank: mock mode was his
# choice, so later commands (cap check, then cap serve, on every launch) do not ask again.
DECLINED = f"# {KEY}=  (left blank: mock mode. set-key.bat adds a key)"
_QUOTES = "\"'`\u201c\u201d\u2018\u2019"


class KeyFormatError(ValueError):
    """What was typed or pasted cannot be an API key; the message says why."""


def clean_key(raw: str) -> str:
    """The key out of whatever was pasted: surrounding whitespace and quotes (straight
    or curly), an `export ` or `CAP_ANTHROPIC_API_KEY=` prefix. Refuses anything that
    is still not plain ASCII without spaces, because httpx cannot send it in a header
    and every call would fail with an encoding error."""
    key = raw
    while True:
        before = key
        key = key.strip().strip(_QUOTES).strip()
        if key.lower().startswith("export "):
            key = key[len("export ") :]
        if key.upper().startswith(f"{KEY}="):
            key = key[len(KEY) + 1 :]
        if key == before:
            break
    if not key.isascii():
        raise KeyFormatError(
            "has characters an API key cannot have (a curly quote, an accented letter or a "
            "non-breaking space); copy it again from console.anthropic.com"
        )
    if any(c.isspace() for c in key):
        raise KeyFormatError("has a space in the middle; copy it again in one piece")
    return key


def _env_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig") if path.exists() else ""


def _raw_key(path: Path) -> str | None:
    for line in _env_text(path).splitlines():
        if line.startswith(f"{KEY}="):
            return line.split("=", 1)[1]
    return None


def read_key(path: Path) -> str | None:
    """The key stored in .env, or None when absent, blank, commented out or unusable."""
    raw = _raw_key(path)
    if raw is None:
        return None
    try:
        return clean_key(raw) or None
    except KeyFormatError:
        return None


def declined(path: Path) -> bool:
    """Hemant left the key prompt blank before (see DECLINED)."""
    return any(
        ln.startswith(f"# {KEY}=") and "left blank" in ln for ln in _env_text(path).splitlines()
    )


def backend(path: Path) -> str:
    return "anthropic" if read_key(path) else "mock"


def _set_key_lines(path: Path, key_line: str, model_backend: str) -> None:
    lines = _env_text(path).splitlines()
    kept = [
        ln
        for ln in lines
        if not ln.lstrip("# ").startswith(f"{KEY}=") and not ln.startswith(f"{BACKEND}=")
    ]
    while kept and not kept[-1].strip():
        kept.pop()
    kept += [f"{BACKEND}={model_backend}", key_line]
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    restrict_to_owner(path)


# Sign-in secrets: three groups of four from letters and digits that cannot be misread
# (no 0/o, 1/l/i), typed on a phone's lower-case keyboard. 31^12 is about 2^59.
SIGNIN = "CAP_SIGNIN_SECRETS"
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
PEOPLE = ("hemant", "layla")  # the approver and the Arabic reviewer (D5, D19)


def new_signin_secret() -> str:
    import secrets

    return "-".join("".join(secrets.choice(_ALPHABET) for _ in range(4)) for _ in range(3))


def _people() -> list[str]:
    from cap.config import Settings

    s = Settings()
    return list(dict.fromkeys([*PEOPLE, *s.approvers, *s.arabic_reviewers]))


def signin_secrets(path: Path) -> dict[str, str]:
    """Person -> sign-in secret, as stored in .env (for the start-up banner)."""
    import json

    for ln in path.read_text(encoding="utf-8").splitlines() if path.exists() else []:
        if ln.startswith(f"{SIGNIN}="):
            try:
                found = json.loads(ln.split("=", 1)[1].strip().strip("'"))
            except ValueError:
                return {}
            return {str(k): str(v) for k, v in found.items()} if isinstance(found, dict) else {}
    return {}


def write_key(path: Path, key: str | None) -> None:
    """Set the key (cleaned; KeyFormatError if unusable), or record that it was left
    blank, keeping every other line of .env as it is."""
    key = clean_key(key or "")
    if key:
        _set_key_lines(path, f"{KEY}={key}", "anthropic")
    else:
        _set_key_lines(path, DECLINED, "mock")


def normalise_encoding(path: Path) -> bool:
    """Rewrite a .env saved in the Windows code page (Notepad's "ANSI") as UTF-8, which is
    how the app reads it. Lossless: every cp1252 byte has one UTF-8 form. True if done."""
    if not path.exists():
        return False
    data = path.read_bytes()
    try:
        data.decode("utf-8")
        return False
    except UnicodeDecodeError:
        pass
    try:
        text = data.decode("cp1252")
    except UnicodeDecodeError:
        return False  # neither; Settings will say what is wrong
    path.write_text(text, encoding="utf-8", newline="")
    return True


def _saved_key(path: Path) -> tuple[str | None, str]:
    """The saved key, tidied in place when it was saved with quotes or a prefix; or
    None and the reason it cannot be used (the unusable key is cleared)."""
    raw = _raw_key(path)
    if raw is None or not raw.strip():
        return None, ""
    try:
        key = clean_key(raw)
    except KeyFormatError as e:
        _set_key_lines(path, f"# {KEY}=", "mock")
        return None, f"The API key saved in .env {e}."
    if key != raw:
        _set_key_lines(path, f"{KEY}={key}", "anthropic")
    return key or None, ""


def ensure_local_secrets(path: Path) -> None:
    """A random approval-signing key and one sign-in secret per person, written once
    into .env, which is then readable by its owner only. The public default key would
    let anyone on the Wi-Fi forge an approval; a shared code would let one reviewer
    sign in as another. The old shared passcode line is dropped."""
    import json
    import secrets

    text = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = [ln for ln in text.splitlines() if not ln.startswith("CAP_LOCAL_PASSCODE=")]
    have = {ln.split("=", 1)[0] for ln in lines if "=" in ln and not ln.startswith("#")}
    if "CAP_APPROVAL_SIGNING_KEY" not in have:
        lines.append(f"CAP_APPROVAL_SIGNING_KEY={secrets.token_hex(32)}")
    current = signin_secrets(path)
    wanted = {p: current.get(p) or new_signin_secret() for p in _people()}
    wanted.update(current)
    if wanted != current:
        lines = [ln for ln in lines if not ln.startswith(f"{SIGNIN}=")]
        lines.append(f"{SIGNIN}={json.dumps(wanted)}")
    if lines != text.splitlines():
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    restrict_to_owner(path)


def read_passcode(path: Path) -> str:
    """Gone: the shared phone passcode was replaced by per-person secrets
    (signin_secrets). Kept, always empty, until the start-up banner stops asking."""
    return ""


def ensure_env(
    path: Path = Path(".env"),
    *,
    force: bool = False,
    ask: Callable[[str], str] | None = None,
    out: Callable[[str], None] = print,
) -> str:
    """Make sure .env exists and (when interactive) holds a key. Returns the backend.

    Asks at most once: a blank answer is remembered (DECLINED) until `cap key`.
    `ask` is the prompt function; None means getpass when stdin is a terminal and no
    prompt at all otherwise (a service or a test never blocks on input)."""
    if not path.exists():
        example = path.with_name(".env.example")
        text = example.read_text(encoding="utf-8") if example.exists() else ""
        path.write_text(text, encoding="utf-8")
    if normalise_encoding(path):
        out(f"{path.name} was saved in the Windows code page; rewrote it as UTF-8.")
    ensure_local_secrets(path)
    have, problem = _saved_key(path)
    if have and not force:
        out(f"API key: saved in {path.resolve()} (run `uv run cap key` to change it)")
        return "anthropic"
    if problem:
        out(problem)
    elif declined(path) and not force:
        out("API key: none (mock mode, sample drafts). Run set-key.bat to add one.")
        return "mock"
    if ask is None:
        if not force and not sys.stdin.isatty():
            return backend(path)  # a service or a test never blocks on input
        ask = getpass.getpass
    out("")
    out(f"Anthropic API key. Typed hidden; saved only to {path.resolve()}.")
    out("Leave blank for mock mode (sample drafts, no network).")
    key = ""
    for _ in range(3):
        try:
            key = clean_key(ask("API key: "))
            break
        except KeyFormatError as e:
            out(f"That key {e}. Try again, or leave it blank for mock mode.")
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
