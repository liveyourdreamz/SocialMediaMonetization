"""`cap check`: every seam with the real world, exercised on this machine.

The test suite proves the code against stand-ins (mock model, silent voice, Linux
CI). Each production failure so far sat at a seam those stand-ins hide: the Windows
shell, Windows newlines, the real model's output. This command runs those seams for
real, here, with this .env, and prints one line per check. The launcher runs it
before the app starts so a broken seam is the first thing in the console, not the
last thing on a parked post.

`--offline` skips anything that needs the network (CI without a key).
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import time
import traceback
from collections.abc import Callable
from pathlib import Path

Check = tuple[str, Callable[[], str]]


class Skip(Exception):  # noqa: N818 - a signal, not an error
    pass


def _console() -> str:
    """Falak's Arabic titles are printed to the console; a cp1252 Windows console would
    raise UnicodeEncodeError in the pipeline thread. cli.main() switches stdout to
    UTF-8; this proves it held."""
    from cap.cli import utf8_console

    utf8_console()
    enc = (sys.stdout.encoding or "").lower()
    try:
        "Falak — فلك".encode(sys.stdout.encoding or "ascii")
    except (UnicodeEncodeError, LookupError) as e:
        raise RuntimeError(f"console encoding {enc or '?'} cannot print Arabic ({e})") from e
    return f"encoding {enc}; Arabic prints"


def _env() -> str:
    from cap.config import get_settings
    from cap.setup import ensure_env, read_key

    ensure_env(Path(".env"), out=lambda _: None)  # creates .env; asks for the key on a laptop
    get_settings.cache_clear()
    s = get_settings()
    p = Path(".env")
    key = read_key(p)
    if s.model_backend.value == "anthropic" and not key:
        raise RuntimeError("backend anthropic but no key in .env; run set-key.bat")
    if key and s.model_backend.value != "anthropic":
        raise RuntimeError("a key is in .env but CAP_MODEL_BACKEND is not anthropic")
    return f"backend {s.model_backend.value}; data dir {Path(s.data_dir).resolve()}"


def _data_dir() -> str:
    from cap.config import get_settings

    d = Path(get_settings().data_dir)
    d.mkdir(parents=True, exist_ok=True)
    probe = d / ".write-probe"
    body = b"line one\nline two\n"
    probe.write_bytes(body)
    if hashlib.sha256(probe.read_bytes()).hexdigest() != hashlib.sha256(body).hexdigest():
        raise RuntimeError("bytes written != bytes read (newline translation?)")
    probe.unlink()
    return "writable; hash round-trip exact"


def _ffmpeg() -> str:
    import subprocess

    from cap.render import _ffmpeg_exe

    exe = _ffmpeg_exe()
    r = subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-200:])
    return r.stdout.splitlines()[0][:60]


def _frames() -> str:
    from cap.render import _raqm, _title_card

    en = _title_card((1920, 1080), "A week with a note app", "Patch", "A", arabic=False)
    ar = _title_card((1920, 1080), "تحلية مياه البحر: كيف تعمل", "Falak", "C", arabic=True)
    if en.size != (1920, 1080) or ar.size != (1920, 1080):
        raise RuntimeError("frame size wrong")
    return f"English and Arabic title cards; Arabic shaping via {'raqm' if _raqm() else 'reshaper+bidi'}"


def _video() -> str:
    from cap.config import get_settings
    from cap.demo.seed import demo_post

    os.environ["CAP_RENDERER"] = "video"
    os.environ["CAP_VOICE"] = "silent"
    get_settings.cache_clear()
    try:
        rec = demo_post()
    finally:
        os.environ.pop("CAP_RENDERER", None)
        os.environ.pop("CAP_VOICE", None)
        get_settings.cache_clear()
    v = Path(rec.asset.uri)
    if rec.asset.kind != "video" or not v.exists() or v.stat().st_size < 10_000:
        raise RuntimeError(f"no usable mp4 at {v}")
    if rec.asset.sha256 != hashlib.sha256(v.read_bytes()).hexdigest():
        raise RuntimeError("asset hash does not match the file on disk")
    return f"{rec.asset.duration_s:.0f}s mp4 with silent voice, hash bound"


def _voice() -> str:
    from cap.render import VOICES, speak

    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "v.mp3"
        t0 = time.time()
        speak("Hello from Patch. This is a voice check.", VOICES[("Patch", "en")], out)
        if not out.exists() or out.stat().st_size < 1000:
            raise RuntimeError("edge-tts produced no audio")
        return f"edge-tts {VOICES[('Patch', 'en')]} in {time.time() - t0:.1f}s"


def _fetch() -> str:
    from cap.fetch import fetch_sources

    docs = fetch_sources(["https://example.com/"], timeout=15)
    if not docs or docs[0]["body"].startswith("[fetch failed"):
        raise RuntimeError(docs[0]["body"] if docs else "no document")
    return f"example.com: {len(docs[0]['body'])} chars of text"


def _model() -> str:
    """One real call through the production client, the real P1 judge prompt, the real
    schema: the exact path that parked the first laptop post."""
    from cap.agents.outputs import P1Output
    from cap.config import get_settings
    from cap.models import AnthropicClient
    from cap.prompts import spec
    from cap.rulebook import load_rulebook

    if get_settings().model_backend.value != "anthropic":
        raise Skip("mock backend (no key); the real model is not exercised")
    client = AnthropicClient()
    sp = spec("P1", "B")
    user = (
        '<variant platform="youtube_long">\n<title>Voice check</title>\n'
        "<script>Hi, I'm Patch, an AI character. Today: a two-line test of the pipeline. "
        "That's it.</script>\n<caption>test</caption>\n<on_screen_text>[]</on_screen_text>\n"
        "<thumbnail_text>test</thumbnail_text>\n</variant>\n<media_manifest>{}</media_manifest>\n"
        "<channel>A</channel>\n<sponsor></sponsor>"
    )
    t0 = time.time()
    res = client.run(sp, user, P1Output, static_blocks=[load_rulebook().as_prompt_text()])
    if res.output is None:
        raise RuntimeError(f"{sp.model}: {res.reason}")
    return (
        f"{res.model or sp.model} answered in {time.time() - t0:.0f}s; verdict "
        f"{res.output.verdict.value}; envelope ok; {res.usage.get('output_tokens', '?')} out tokens"
    )


def _youtube() -> str:
    from cap.config import get_settings

    s = get_settings()
    if s.publisher != "youtube":
        raise Skip(f"publisher is '{s.publisher}' (local folder); YouTube not in use yet")
    tok = Path(s.data_dir) / "youtube-token.json"
    if not tok.exists():
        raise RuntimeError("publisher youtube but no token; run `uv run cap youtube auth`")
    return "token present"


def run(offline: bool = False, out: Callable[[str], None] = print) -> int:
    checks: list[Check] = [
        ("console", _console),
        (".env and key", _env),
        ("data folder", _data_dir),
        ("ffmpeg", _ffmpeg),
        ("frames (fonts, Arabic)", _frames),
        ("video render (silent)", _video),
        ("voice (edge-tts)", _voice),
        ("source fetch", _fetch),
        ("model (real call)", _model),
        ("youtube", _youtube),
    ]
    network = {"voice (edge-tts)", "source fetch", "model (real call)"}
    failed = 0
    out("checking this machine against the real services:")
    for name, fn in checks:
        if offline and name in network:
            out(f"  skip {name:24s} offline")
            continue
        try:
            detail = fn()
            out(f"  ok   {name:24s} {detail}")
        except Skip as e:
            out(f"  skip {name:24s} {e}")
        except Exception as e:  # noqa: BLE001 - every failure is reported, none hides the rest
            failed += 1
            msg = f"{type(e).__name__}: {e}" if not isinstance(e, RuntimeError) else str(e)
            out(f"  FAIL {name:24s} {msg[:300]}")
            if os.environ.get("CAP_CHECK_TRACE"):
                out(traceback.format_exc())
    out("")
    out(
        "all checks passed"
        if not failed
        else f"{failed} check(s) FAILED; fix these before filing a brief"
    )
    return 1 if failed else 0
