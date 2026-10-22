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

    ensure_env(Path(".env"), out=lambda s: print(f"       {s}"))  # creates .env; asks for the key
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
    from cap.render import _raqm
    from cap.render import scenes as sc

    lay = sc.layout((1920, 1080))
    patch_plan = sc.plan(
        ["On day 3 the sync failed twice.", "Our test measured 12 seconds on average."],
        character="Patch", title="We tested Notely", topic="Notely test", on_screen=[], media=[],
    )  # fmt: skip
    en = sc.frame(
        patch_plan[1], 10, 48, 2.0, lay, character="Patch", channel="A", level=0.7, arabic=False
    )
    falak_plan = sc.plan(
        ["من المدار، تبدو محطة التحلية كنقطة بيضاء.", "تنتج 500 مليون لتر يومياً."],
        character="Falak", title="تحلية المياه", topic="تحلية", on_screen=[], media=[], arabic=True,
    )  # fmt: skip
    ar = sc.frame(
        falak_plan[1], 5, 48, 1.0, lay, character="Falak", channel="C", level=0.3, arabic=True
    )
    if en.size != (1920, 1080) or ar.size != (1920, 1080):
        raise RuntimeError("frame size wrong")
    kinds = [p.kind for p in patch_plan] + [p.kind for p in falak_plan]
    return f"Patch and Falak scenes drawn ({', '.join(kinds)}); Arabic shaping via {'raqm' if _raqm() else 'reshaper+bidi'}"


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
    """Real pages the way research reads them: a plain site and a Wikipedia article
    (served compressed, read through its REST API)."""
    from cap.fetch import fetch_sources

    urls = ["https://example.com/", "https://en.wikipedia.org/wiki/Silk"]
    docs = fetch_sources(urls, timeout=15)
    bad = [d for d in docs if d["body"].startswith("[fetch failed")]
    if len(docs) != len(urls) or bad:
        raise RuntimeError("; ".join(f"{d['url']}: {d['body']}" for d in bad) or "no document")
    return " · ".join(f"{d['url'].split('/')[2]}: {len(d['body'])} chars" for d in docs)


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


def _characters() -> str:
    """The real critic (P3) once per character, with the block a real brief uses. The
    mock never reads a character block, so a gap in one (22 Oct: Falak had no speaking
    rate) only shows when the real model refuses. A refusal here names the gap."""
    from cap.agents import AgentBlockedError, critique
    from cap.characters import CHARACTER_BLOCKS, KNOWLEDGE_NOTE
    from cap.config import get_settings
    from cap.demo.seed import new_brief
    from cap.models import AnthropicClient

    if get_settings().model_backend.value != "anthropic":
        raise Skip("mock backend (no key); the character blocks are not exercised")
    client = AnthropicClient()
    sample = {
        "A": ("How a rainbow works", "Let's take it apart. Sunlight enters a raindrop and bends."),
        "B": ("How cocoons work", "From orbit, here's what I see. A caterpillar spins silk."),
        "C": ("كيف تعمل الشرانق", "من المدار، هذا ما أراه. اليرقة تغزل الحرير."),
    }
    done = []
    for ch, (topic, script) in sample.items():
        rec = new_brief(ch, topic)
        rec.draft.title, rec.draft.script = topic, "[[HEMANT_OPENING_LINE]] " + script
        try:
            critique(rec, client, CHARACTER_BLOCKS[ch] + "\n\n" + KNOWLEDGE_NOTE)
        except AgentBlockedError as e:
            raise RuntimeError(f"channel {ch}: {str(e)[:300]}") from e
        done.append(f"{ch} {rec.draft.critic_verdict}")
    return "critic ran on every character: " + ", ".join(done)


def _youtube() -> str:
    """The Connections page is what publishing uses; the old CAP_PUBLISHER setting only
    matters for the scheduler. Reports the connected account, never a token."""
    from cap import connections
    from cap.config import get_settings

    if connections.youtube_credentials_json() is not None:
        account = connections.load("youtube").get("account") or {}
        name = account.get("name") or account.get("title") or "connected"
        return f"connected on Connections ({name}); run youtube-test.bat to try an upload"
    s = get_settings()
    if s.publisher == "youtube" and (Path(s.data_dir) / "youtube-token.json").exists():
        return "connected with the older sign-in file; run youtube-test.bat to try an upload"
    raise Skip("not connected yet: open Connections in the app and connect YouTube")


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
        ("characters (real critic)", _characters),
        ("youtube", _youtube),
    ]
    network = {
        "voice (edge-tts)",
        "source fetch",
        "model (real call)",
        "characters (real critic)",
    }
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


FULL_RUN_TOPICS = {
    "A": "How a rainbow works",
    "B": "How cocoons work",
    "C": "كيف تعمل الشرانق",
}


def full_run(out: Callable[[str], None] = print, channels: str = "ABC") -> int:
    """One real brief per channel through the whole pipeline: the real model at every
    step, research, the four gates, the voice, the video, then a stand-in approval and
    a publish to a local check folder. Nothing goes to your queue or to any platform.
    It reports the step and the reason for the first thing that fails in each run."""
    import tempfile

    from cap.approval import token as tok
    from cap.approval.app import Store
    from cap.config import get_settings
    from cap.demo.seed import run_brief
    from cap.publisher import LocalFolderAdapter, publish_to
    from cap.schema.post_record import PostStatus

    s = get_settings()
    if s.model_backend.value != "anthropic":
        out("full run needs your API key (the mock backend proves nothing new here)")
        return 1
    out(
        f"full run: one {s.video_length_s}s post per channel, real model, up to "
        f"{s.max_claims} claims; this takes a few minutes and uses API credit"
    )
    failed = 0
    dest = Path(tempfile.mkdtemp(prefix="cap-full-run-"))
    for ch in channels:
        topic = FULL_RUN_TOPICS[ch]
        t0 = time.time()
        try:
            rec = run_brief(Store(), ch, topic)
        except Exception as e:  # noqa: BLE001 - reported with the channel
            failed += 1
            out(f"  FAIL {ch} {topic}: crashed in the pipeline: {type(e).__name__}: {e}"[:400])
            if os.environ.get("CAP_CHECK_TRACE"):
                out(traceback.format_exc())
            continue
        took = time.time() - t0
        if rec.status != PostStatus.AWAITING_APPROVAL:
            failed += 1
            step = rec.flags.stopped_at or rec.status.value
            out(f"  FAIL {ch} {topic}: stopped at {step}: {rec.flags.stop_reason or ''}"[:400])
            for line in rec.history[-4:]:
                out(f"         {line[:200]}")
            continue
        try:
            rec.status = PostStatus.APPROVED  # stand-in approval, in memory only
            t = tok.issue(rec.post_id, rec.asset.sha256, s.approvers[0], "approve", canary=False)
            publish_to(rec, Path(rec.asset.uri), t, [LocalFolderAdapter(dest)])
            if rec.status != PostStatus.PUBLISHED:
                raise RuntimeError(f"status after publish: {rec.status.value}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            out(f"  FAIL {ch} {topic}: publish step: {type(e).__name__}: {e}"[:400])
            continue
        unconfirmed = sum(1 for c in rec.draft.claims if c.status != "supported")
        out(
            f"  ok   {ch} {topic}: {took:.0f}s end to end · {rec.asset.duration_s:.0f}s video"
            f"{' + Short' if rec.asset.short_uri else ''} · {len(rec.draft.claims)} claims "
            f"({unconfirmed} unconfirmed) · published to {dest / rec.post_id}"
        )
    out("")
    out(
        "full run passed on every channel"
        if not failed
        else f"{failed} channel(s) FAILED; the line above each says where and why"
    )
    return 1 if failed else 0
