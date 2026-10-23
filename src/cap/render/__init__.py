"""The media renderer (agent 11, plain code), scrappy edition.

Voice: Microsoft neural voices through edge-tts (free, no key; English and Arabic).
    The PRD's ElevenLabs private voices (D21) replace this behind the same function.
Picture: scenes (cap.render.scenes). The character (Patch or Falak, drawn from the
    Anchor Registry, animated, speaking in time with the voice) stands beside a screen
    panel that shows what the paragraph is about: the app failing to sync, a stopwatch
    to the measured number, the local-then-cloud flow, the verdict stamp, a figure; or
    a screenshot uploaded with the brief. The spoken sentence runs underneath; the
    character tag and the "AI character" label are on every frame (R-16); a sources
    card ends it. No generated faces, no stock footage, nothing pretending to be a
    person; reconstructed app views are labelled as reconstructions.
Assembly: ffmpeg, the static binary that imageio-ffmpeg ships.
Output: the long-form 1920x1080 mp4, a 1080x1920 Short when the post has a Short
    cut, a thumbnail png. Twice per post: the review render before approval (the
    opening line a visible placeholder) and the final render after it (the line
    spoken); the approval token binds the final files through the release manifest
    (cap.release). C2PA signing (D29) is not done here yet and the preview says so.

Everything the picture shows comes from the approved record; the renderer never
invents words. Rendering is deterministic apart from the voice service.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw

from cap.config import get_settings
from cap.policy import AI_ROLE_DISCLOSURE
from cap.render.text import shape_text, text_font, text_kw, wrap_lines
from cap.schema.post_record import Platform, PostRecord

OPENING_SLOT = "[[HEMANT_OPENING_LINE]]"
OPENING_PLACEHOLDER = "Your opening line goes here"
PLACEHOLDER_UNIT = f"[{OPENING_PLACEHOLDER}]"  # the review render's stand-in paragraph
Stage = Literal["review", "final"]

# Proof tokens (light theme), copied so the renderer has no web dependency.
GROUND, SURFACE, INK, MUTED, LINE, ACCENT = (
    (0xF6, 0xF4, 0xEF),
    (0xFF, 0xFF, 0xFF),
    (0x1B, 0x1A, 0x17),
    (0x5C, 0x59, 0x55),
    (0xDD, 0xD9, 0xD0),
    (0x1F, 0x5F, 0x73),
)

VOICES = {  # character -> edge-tts voice; ElevenLabs ids replace these (D21)
    ("Patch", "en"): "en-GB-RyanNeural",
    ("Falak", "en"): "en-GB-SoniaNeural",
    ("Falak", "ar"): "ar-AE-FatimaNeural",
}


@dataclass
class RenderResult:
    video: Path
    short: Path | None
    thumbnail: Path
    sha256: str
    duration_s: float
    voice: str


class RenderError(RuntimeError):
    pass


# ---------------------------------------------------------------- text helpers


def script_text(record: PostRecord, cut: str | None = None, stage: Stage = "review") -> str:
    """The narration for one stage (review C1). The review render, made before
    approval, marks the opening line's moment with a visible placeholder paragraph;
    the final render, made after approval, speaks the approver's line there (and opens
    with it when a script lost its slot). A final render without a line refuses."""
    text = cut or record.draft.script
    if stage == "review":
        return text.replace(OPENING_SLOT, f"\n\n{PLACEHOLDER_UNIT}\n\n").strip()
    human = record.human_contribution.text.strip()
    if not human:
        raise RenderError("the final render needs the approver's opening line")
    if OPENING_SLOT not in text:
        return f"{human} {text.strip()}"
    return text.replace(OPENING_SLOT, human).strip()


def _clean(p: str) -> str:
    """Strip markdown the drafter may add (headings, emphasis, rules): it would be read
    aloud and shown on a slide otherwise."""
    p = re.sub(r"^\s*(#{1,6}|[-*_]{3,})\s*", "", p, flags=re.M)
    p = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", p)
    return p.strip()


def paragraphs(text: str) -> list[str]:
    parts = [_clean(p) for p in re.split(r"\n\s*\n", text)]
    parts = [p for p in parts if re.search(r"\w", p)]  # nothing to say or show otherwise
    out: list[str] = []
    for p in parts:  # keep each slide readable: split long paragraphs at sentences
        if len(p) <= 420:
            out.append(p)
            continue
        buf = ""
        for sent in re.split(r"(?<=[.!?؟])\s+", p):
            if len(buf) + len(sent) > 420 and buf:
                out.append(buf.strip())
                buf = ""
            buf += sent + " "
        if buf.strip():
            out.append(buf.strip())
    return out


# ---------------------------------------------------------------- frames


def _frame(size: tuple[int, int]) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    im = Image.new("RGB", size, GROUND)
    return im, ImageDraw.Draw(im)


def _chrome(
    draw: ImageDraw.ImageDraw, size: tuple[int, int], character: str, channel: str, arabic: bool
) -> None:
    """The constant parts of every frame: the character tag and the AI label."""
    w, h = size
    m = int(w * 0.05)
    tag = f"{channel} · {character}".upper()
    draw.text((m, m), tag, font=text_font(int(h * 0.026), bold=True), fill=ACCENT)
    label = AI_ROLE_DISCLOSURE.rstrip(".")
    f = text_font(int(h * 0.024))
    tw = draw.textlength(label, font=f)
    draw.text((w - m - tw, m), label, font=f, fill=MUTED)
    draw.line([(m, h - m), (w - m, h - m)], fill=LINE, width=2)


def _title_card(
    size: tuple[int, int], title: str, character: str, channel: str, arabic: bool
) -> Image.Image:
    im, d = _frame(size)
    w, h = size
    m = int(w * 0.08)
    _chrome(d, size, character, channel, arabic)
    f = text_font(int(h * (0.075 if w > h else 0.055)), bold=True, arabic=arabic)
    lines = wrap_lines(d, title, f, w - 2 * m, arabic)
    y = h // 2 - len(lines) * int(f.size * 1.15) // 2
    for ln in lines:
        txt = shape_text(ln, arabic)
        x = w - m - d.textlength(txt, font=f, **text_kw(arabic)) if arabic else m
        d.text((x, y), txt, font=f, fill=INK, **text_kw(arabic))
        y += int(f.size * 1.15)
    return im


def _slide(
    size: tuple[int, int], text: str, n: int, total: int, character: str, channel: str, arabic: bool
) -> Image.Image:
    im, d = _frame(size)
    w, h = size
    m = int(w * 0.08)
    _chrome(d, size, character, channel, arabic)
    f = text_font(int(h * (0.042 if w > h else 0.034)), arabic=arabic)
    lines = wrap_lines(d, text, f, w - 2 * m, arabic)
    lh = int(f.size * 1.5)
    y = max(int(h * 0.18), h // 2 - len(lines) * lh // 2)
    # the speaking card: a surface block behind the text, Proof's hairline around it
    pad = int(w * 0.03)
    d.rounded_rectangle(
        [m - pad, y - pad, w - m + pad, y + len(lines) * lh + pad],
        radius=int(w * 0.01),
        fill=SURFACE,
        outline=LINE,
        width=2,
    )
    for ln in lines:
        txt = shape_text(ln, arabic)
        x = w - m - d.textlength(txt, font=f, **text_kw(arabic)) if arabic else m
        d.text((x, y), txt, font=f, fill=INK, **text_kw(arabic))
        y += lh
    d.text(
        (m, h - int(w * 0.05) - int(h * 0.04)),
        f"{n} / {total}",
        font=text_font(int(h * 0.022)),
        fill=MUTED,
    )
    return im


def _end_card(
    size: tuple[int, int], sources: list[str], character: str, channel: str, arabic: bool
) -> Image.Image:
    im, d = _frame(size)
    w, h = size
    m = int(w * 0.08)
    _chrome(d, size, character, channel, arabic)
    d.text((m, int(h * 0.2)), "Sources", font=text_font(int(h * 0.05), bold=True), fill=INK)
    f = text_font(int(h * 0.028))
    y = int(h * 0.32)
    for s in sources[:8]:
        for ln in wrap_lines(d, s, f, w - 2 * m)[:2]:
            d.text((m, y), ln, font=f, fill=MUTED)
            y += int(f.size * 1.4)
        y += int(f.size * 0.4)
    d.text((m, h - int(h * 0.2)), AI_ROLE_DISCLOSURE, font=text_font(int(h * 0.03)), fill=INK)
    return im


# ---------------------------------------------------------------- voice


async def _tts(text: str, voice: str, out: Path) -> None:
    import edge_tts

    await edge_tts.Communicate(text, voice).save(str(out))


def speak(text: str, voice: str, out: Path) -> None:
    """One paragraph -> one mp3. Swappable: ElevenLabs lands here."""
    asyncio.run(_tts(text, voice, out))


def silent_voice(text: str, voice: str, out: Path) -> None:
    """Offline stand-in: 0.4 s of silence per word, so timings stay plausible."""
    _silence(max(1.0, 0.4 * len(text.split())), out)


def _silence(seconds: float, out: Path) -> None:
    _ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=24000:cl=mono",
            "-t",
            f"{seconds:.2f}",
            "-q:a",
            "9",
            "-acodec",
            "libmp3lame",
            str(out),
        ]
    )


def _duration(path: Path) -> float:
    exe = _ffprobe()
    if exe:
        r = subprocess.run(
            [exe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True,
            text=True,
        )
        try:
            return float(r.stdout.strip())
        except ValueError:
            pass
    # fall back to ffmpeg's own report
    exe = _ffmpeg_exe()
    r = subprocess.run(
        [exe, "-i", str(path)], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    if not m:
        raise RenderError(f"cannot read duration of {path.name}")
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


# ---------------------------------------------------------------- ffmpeg


def _ffmpeg_exe() -> str:
    import imageio_ffmpeg

    return str(imageio_ffmpeg.get_ffmpeg_exe())


def _ffprobe() -> str | None:
    import shutil

    return shutil.which("ffprobe")


def _ffmpeg(args: list[str]) -> None:
    r = subprocess.run(
        [_ffmpeg_exe(), "-y", "-loglevel", "error", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if r.returncode != 0:
        raise RenderError(r.stderr.strip()[-800:])


def _assemble(slides: list[tuple[Path, Path]], out: Path, size: tuple[int, int]) -> float:
    """[(png, mp3)] -> mp4: each slide shown for the length of its audio."""
    parts: list[Path] = []
    total = 0.0
    for i, (png, mp3) in enumerate(slides):
        dur = _duration(mp3)
        total += dur
        seg = out.with_name(f"{out.stem}.seg{i:03d}.mp4")
        _ffmpeg(
            [
                "-loop",
                "1",
                "-framerate",
                "24",
                "-i",
                str(png),
                "-i",
                str(mp3),
                "-t",
                f"{dur:.2f}",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-preset",
                "veryfast",
                "-vf",
                f"scale={size[0]}:{size[1]}",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-shortest",
                str(seg),
            ]
        )
        parts.append(seg)
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    _ffmpeg(
        [
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(lst),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(out),
        ]
    )
    for p in parts:
        p.unlink(missing_ok=True)
    lst.unlink(missing_ok=True)
    return total


def _concat(parts: list[Path], out: Path) -> None:
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    _ffmpeg(
        [
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(lst),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(out),
        ]
    )
    lst.unlink(missing_ok=True)


# ---------------------------------------------------------------- entry point


def _encode_scene(
    frames_iter,
    n: int,
    size: tuple[int, int],
    mp3: Path,
    out: Path,  # type: ignore[no-untyped-def]
) -> None:
    """Pipe raw frames into ffmpeg with the scene's audio -> one mp4 segment."""
    from cap.render.scenes import FPS

    cmd = [
        _ffmpeg_exe(), "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{size[0]}x{size[1]}", "-r", str(FPS), "-i", "-",
        "-i", str(mp3),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "22",
        "-c:a", "aac", "-b:a", "128k", "-shortest", str(out),
    ]  # fmt: skip
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdin is not None
    try:
        for im in frames_iter:
            proc.stdin.write(im.tobytes())
    except BrokenPipeError:
        pass
    finally:
        proc.stdin.close()
    err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
    if proc.wait() != 0:
        raise RenderError(err.strip()[-800:] or "ffmpeg failed while encoding a scene")


def _media_files(record: PostRecord) -> list[Path]:
    """Screenshots uploaded with the brief, in order, if any (cap-data/media/<post>/)."""
    d = Path(get_settings().data_dir) / "media" / record.post_id
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"))


def render(  # type: ignore[no-untyped-def]  # noqa: C901 - M1 step 4 splits it
    record: PostRecord, out_dir: Path, *, voice_fn=speak, stage: Stage = "review"
) -> RenderResult:
    """Render one stage of the package into <out_dir>/<post_id>/ (review) or
    <out_dir>/<post_id>/final/ (final, after approval). `voice_fn` is injectable so
    tests run offline. The review render shows a silent "your opening line goes
    here" card where the approver's line will be; the final render speaks it."""
    from cap.render import scenes as sc

    s = get_settings()
    channel = record.channel_id.value
    character = s.channel_characters.get(channel, "")
    lang = s.channel_languages.get(channel, "en")
    arabic = lang == "ar"
    voice = VOICES.get((character, lang), "en-GB-RyanNeural")
    work = out_dir / record.post_id / ("final" if stage == "final" else "")
    work.mkdir(parents=True, exist_ok=True)
    from cap.domain.verdict import confirmed_sources

    sources = confirmed_sources(record)  # the one filter: supported claims, no test log
    tool = sc.tool_name(record.draft.title, record.brief.topic) if character == "Patch" else ""

    def build(text: str, title: str, size: tuple[int, int], name: str) -> tuple[Path, float]:
        paras = paragraphs(text)
        if not paras:
            raise RenderError("nothing to say: the script is empty")
        lay = sc.layout(size)
        said = sc.units(paras)
        # the review placeholder is its own silent card, planned around (not through)
        # the scene rules so the review and final cuts share their pictures
        slot_at = said.index(PLACEHOLDER_UNIT) if PLACEHOLDER_UNIT in said else None
        if slot_at is not None:
            said.pop(slot_at)
        plan = sc.plan(
            [title, *said],
            character=character or "Patch",
            title=title,
            topic=record.brief.topic,
            on_screen=list(record.draft.on_screen_text),
            media=_media_files(record),
            arabic=arabic,
        )
        plan[0].kind = "talk"  # the title is spoken over the character, then the story
        if slot_at is not None:
            plan.insert(
                slot_at + 1,
                sc.Scene(kind="opening_slot", text=PLACEHOLDER_UNIT, sentences=[PLACEHOLDER_UNIT]),
            )
        plan.append(sc.Scene(kind="sources", text="", sentences=[]))
        segs: list[Path] = []
        t_run = 0.0
        total = 0.0
        for i, scene in enumerate(plan):
            mp3 = work / f"{name}-{i:03d}.mp3"
            silent = scene.kind in ("sources", "opening_slot")
            if silent:
                _silence(3.0, mp3)
            else:
                voice_fn(scene.text, voice, mp3)
            dur = _duration(mp3)
            n = max(2, int(round(dur * sc.FPS)))
            levels = [0.0] * n if silent else sc.loudness(mp3, n)
            seg = work / f"{name}-{i:03d}.mp4"

            def frames(scene=scene, n=n, levels=levels, t0=t_run):  # type: ignore[no-untyped-def]
                for k in range(n):
                    yield sc.frame(
                        scene, k, n, t0 + k / sc.FPS, lay,
                        character=character or "Patch", channel=channel,
                        level=levels[k], arabic=arabic, sources=sources,
                    )  # fmt: skip

            _encode_scene(frames(), n, size, mp3, seg)
            segs.append(seg)
            t_run += n / sc.FPS
            total += dur
            mp3.unlink(missing_ok=True)
        video = work / f"{name}.mp4"
        _concat(segs, video)
        for p_ in segs:
            p_.unlink(missing_ok=True)
        return video, total

    video, duration = build(
        script_text(record, stage=stage), record.draft.title, (1920, 1080), "long"
    )
    short: Path | None = None
    cut = next(
        (v for v in record.variants if v.platform == Platform.YOUTUBE_SHORT and v.script_cut), None
    )
    if cut:
        short, _ = build(
            script_text(record, cut.script_cut, stage),
            cut.title or record.draft.title,
            (1080, 1920),
            "short",
        )
    thumb = work / "thumbnail.png"
    sc.thumbnail(
        (1280, 720),
        record.draft.thumbnail_text or record.draft.title,
        character or "Patch",
        channel,
        tool,
        arabic,
    ).save(thumb)
    digest = hashlib.sha256(video.read_bytes()).hexdigest()
    return RenderResult(
        video=video, short=short, thumbnail=thumb, sha256=digest, duration_s=duration, voice=voice
    )
