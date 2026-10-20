"""The media renderer (agent 11, plain code), scrappy edition.

Voice: Microsoft neural voices through edge-tts (free, no key; English and Arabic).
    The PRD's ElevenLabs private voices (D21) replace this behind the same function.
Picture: a typographic explainer in Proof. Title card; one slide per paragraph with
    the spoken text readable on screen; the character's name and the "AI character"
    label on every frame (R-16, disclosure); an end card with the sources. No
    generated faces, no stock footage, nothing pretending to be a person.
Assembly: ffmpeg, the static binary that imageio-ffmpeg ships.
Output: the long-form 1920x1080 mp4 (what the approval token binds to), a 1080x1920
    Short when the post has a Short cut, a thumbnail png. C2PA signing (D29) is not
    done here yet and the preview says so.

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

from PIL import Image, ImageDraw, ImageFont

from cap.config import get_settings
from cap.policy import AI_ROLE_DISCLOSURE
from cap.schema.post_record import Platform, PostRecord

FONTS = Path(__file__).resolve().parent / "fonts"
OPENING_SLOT = "[[HEMANT_OPENING_LINE]]"

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


def script_text(record: PostRecord, cut: str | None = None) -> str:
    text = cut or record.draft.script
    human = record.human_contribution.text.strip()
    return text.replace(OPENING_SLOT, human if human else "").strip()


def paragraphs(text: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
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


def _font(size: int, bold: bool = False, arabic: bool = False) -> ImageFont.FreeTypeFont:
    name = ("IBMPlexSansArabic" if arabic else "IBMPlexSans") + (
        "-SemiBold" if bold else "-Regular"
    )
    return ImageFont.truetype(str(FONTS / f"{name}.ttf"), size)


def _raqm() -> bool:
    from PIL import features

    return bool(features.check("raqm"))


def _shape(text: str, arabic: bool) -> str:
    """Pillow with libraqm shapes Arabic itself (direction='rtl' below); without it,
    pre-shape with arabic_reshaper and reorder with python-bidi. Never both."""
    if not arabic or _raqm():
        return text
    import arabic_reshaper
    from bidi.algorithm import get_display

    return str(get_display(arabic_reshaper.reshape(text)))


def _kw(arabic: bool) -> dict:
    return {"direction": "rtl", "language": "ar"} if arabic and _raqm() else {}


def _wrap(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    width: int,
    arabic: bool = False,
) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(_shape(trial, arabic), font=font, **_kw(arabic)) <= width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


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
    draw.text((m, m), tag, font=_font(int(h * 0.026), bold=True), fill=ACCENT)
    label = AI_ROLE_DISCLOSURE.rstrip(".")
    f = _font(int(h * 0.024))
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
    f = _font(int(h * (0.075 if w > h else 0.055)), bold=True, arabic=arabic)
    lines = _wrap(d, title, f, w - 2 * m, arabic)
    y = h // 2 - len(lines) * int(f.size * 1.15) // 2
    for ln in lines:
        txt = _shape(ln, arabic)
        x = w - m - d.textlength(txt, font=f, **_kw(arabic)) if arabic else m
        d.text((x, y), txt, font=f, fill=INK, **_kw(arabic))
        y += int(f.size * 1.15)
    return im


def _slide(
    size: tuple[int, int], text: str, n: int, total: int, character: str, channel: str, arabic: bool
) -> Image.Image:
    im, d = _frame(size)
    w, h = size
    m = int(w * 0.08)
    _chrome(d, size, character, channel, arabic)
    f = _font(int(h * (0.042 if w > h else 0.034)), arabic=arabic)
    lines = _wrap(d, text, f, w - 2 * m, arabic)
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
        txt = _shape(ln, arabic)
        x = w - m - d.textlength(txt, font=f, **_kw(arabic)) if arabic else m
        d.text((x, y), txt, font=f, fill=INK, **_kw(arabic))
        y += lh
    d.text(
        (m, h - int(w * 0.05) - int(h * 0.04)),
        f"{n} / {total}",
        font=_font(int(h * 0.022)),
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
    d.text((m, int(h * 0.2)), "Sources", font=_font(int(h * 0.05), bold=True), fill=INK)
    f = _font(int(h * 0.028))
    y = int(h * 0.32)
    for s in sources[:8]:
        for ln in _wrap(d, s, f, w - 2 * m)[:2]:
            d.text((m, y), ln, font=f, fill=MUTED)
            y += int(f.size * 1.4)
        y += int(f.size * 0.4)
    d.text((m, h - int(h * 0.2)), AI_ROLE_DISCLOSURE, font=_font(int(h * 0.03)), fill=INK)
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
    r = subprocess.run([exe, "-i", str(path)], capture_output=True, text=True)
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
        [_ffmpeg_exe(), "-y", "-loglevel", "error", *args], capture_output=True, text=True
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
    _ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)])
    for p in parts:
        p.unlink(missing_ok=True)
    lst.unlink(missing_ok=True)
    return total


# ---------------------------------------------------------------- entry point


def render(record: PostRecord, out_dir: Path, *, voice_fn=speak) -> RenderResult:  # type: ignore[no-untyped-def]
    """Render the approved package. `voice_fn` is injectable so tests run offline."""
    s = get_settings()
    channel = record.channel_id.value
    character = s.channel_characters.get(channel, "")
    lang = s.channel_languages.get(channel, "en")
    arabic = lang == "ar"
    voice = VOICES.get((character, lang), "en-GB-RyanNeural")
    work = out_dir / record.post_id
    work.mkdir(parents=True, exist_ok=True)

    def build(text: str, title: str, size: tuple[int, int], name: str) -> tuple[Path, float]:
        paras = paragraphs(text)
        if not paras:
            raise RenderError("nothing to say: the script is empty")
        slides: list[tuple[Path, Path]] = []
        # title card, spoken as the title
        png = work / f"{name}-000.png"
        _title_card(size, title, character, channel, arabic).save(png)
        mp3 = work / f"{name}-000.mp3"
        voice_fn(title, voice, mp3)
        slides.append((png, mp3))
        for i, p in enumerate(paras, 1):
            png = work / f"{name}-{i:03d}.png"
            _slide(size, p, i, len(paras), character, channel, arabic).save(png)
            mp3 = work / f"{name}-{i:03d}.mp3"
            voice_fn(p, voice, mp3)
            slides.append((png, mp3))
        sources = sorted(
            {
                c.source_url
                for c in record.draft.claims
                if c.source_url and not c.source_url.startswith("testlog://")
            }
        )
        png = work / f"{name}-end.png"
        _end_card(size, sources, character, channel, arabic).save(png)
        mp3 = work / f"{name}-end.mp3"
        _silence(3.0, mp3)
        slides.append((png, mp3))
        video = work / f"{name}.mp4"
        dur = _assemble(slides, video, size)
        for p_, a_ in slides:
            p_.unlink(missing_ok=True)
            a_.unlink(missing_ok=True)
        return video, dur

    video, duration = build(script_text(record), record.draft.title, (1920, 1080), "long")
    short: Path | None = None
    cut = next(
        (v for v in record.variants if v.platform == Platform.YOUTUBE_SHORT and v.script_cut), None
    )
    if cut:
        short, _ = build(
            script_text(record, cut.script_cut),
            cut.title or record.draft.title,
            (1080, 1920),
            "short",
        )
    thumb = work / "thumbnail.png"
    _title_card(
        (1280, 720), record.draft.thumbnail_text or record.draft.title, character, channel, arabic
    ).save(thumb)
    digest = hashlib.sha256(video.read_bytes()).hexdigest()
    return RenderResult(
        video=video, short=short, thumbnail=thumb, sha256=digest, duration_s=duration, voice=voice
    )
