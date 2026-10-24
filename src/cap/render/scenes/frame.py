"""Frames: one image per frame of a scene, the loudness that moves the character, the thumbnail."""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

from cap.render.characters import draw_falak, draw_patch
from cap.render.scenes.draw import Layout, draw_chrome, draw_subtitle, layout, panel_frame
from cap.render.scenes.model import ACCENT, GROUND, INK, Scene
from cap.render.scenes.panels import draw_panel, sources_panel
from cap.render.text import shape_text, text_font, text_kw, wrap_lines


def frame(
    sc: Scene,
    i: int,
    n: int,
    t_total: float,
    lay: Layout,
    *,
    character: str,
    channel: str,
    level: float,
    arabic: bool,
    sources: list[str] | None = None,
) -> Image.Image:
    """Frame i of n for the scene; t_total is the running time in the whole episode."""
    im = Image.new("RGB", lay.size, GROUND)
    d = ImageDraw.Draw(im)
    draw_chrome(d, lay, character, channel)
    f = 1.0 if sc.hold else i / max(1, n - 1)
    inner = panel_frame(d, lay)
    if sc.kind == "sources":
        sources_panel(d, inner, sources or [])
    else:
        draw_panel(d, im, sc, inner, f, t_total, arabic)
    mood = sc.mood if f > 0.15 else "idle"
    if character == "Falak":
        draw_falak(im, lay.char_box, t_total, level, mood)
    else:
        reading = (
            f"{sc.number:.1f}"
            if sc.number is not None and sc.number < 100
            else f"{sc.number:.0f}"
            if sc.number is not None
            else "--.-"
        )
        draw_patch(im, lay.char_box, t_total, level, mood, reading)
    if sc.kind != "sources" and sc.sentences:
        k = min(len(sc.sentences) - 1, int(f * len(sc.sentences)))
        draw_subtitle(d, lay, sc.sentences[k], arabic)
    return im


def loudness(mp3: Path, n_frames: int) -> list[float]:
    """Per-frame voice level 0..1 from the audio, so the character speaks in time."""
    import array
    import subprocess

    from cap.render.media import ffmpeg_exe

    r = subprocess.run(
        [
            ffmpeg_exe(),
            "-v",
            "error",
            "-i",
            str(mp3),
            "-f",
            "s16le",
            "-ac",
            "1",
            "-ar",
            "8000",
            "-",
        ],
        capture_output=True,
    )
    if r.returncode != 0 or not r.stdout:
        return [0.0] * n_frames
    a = array.array("h")
    a.frombytes(r.stdout[: len(r.stdout) - len(r.stdout) % 2])
    per = max(1, len(a) // max(1, n_frames))
    rms = []
    for k in range(n_frames):
        chunk = a[k * per : (k + 1) * per]
        rms.append(math.sqrt(sum(v * v for v in chunk) / len(chunk)) if len(chunk) else 0.0)
    peak = sorted(rms)[int(len(rms) * 0.95)] if rms else 1.0
    out, prev = [], 0.0
    for v in rms:
        lv = min(1.0, v / peak) if peak else 0.0
        prev = prev * 0.5 + lv * 0.5
        out.append(prev)
    return out


def thumbnail(
    size: tuple[int, int], text: str, character: str, channel: str, tool: str, arabic: bool
) -> Image.Image:
    """The character beside the thumbnail text, big, in the verdict mood."""
    im = Image.new("RGB", size, GROUND)
    d = ImageDraw.Draw(im)
    w, h = size
    lay = layout(size)
    draw_chrome(d, lay, character, channel)
    box = (int(w * 0.02), int(h * 0.16), int(w * 0.3), int(h * 0.76))
    (draw_falak if character == "Falak" else draw_patch)(
        im, box, 1.3, 0.6, "keep" if character == "Patch" else "idle"
    )
    fs = text_font(int(h * 0.15), bold=True, arabic=arabic)
    lines = wrap_lines(d, text, fs, int(w * 0.5), arabic)[:3]
    block = len(lines) * fs.size * 1.12 + (h * 0.09 if tool else 0)
    yy = h * 0.52 - block / 2
    x_text = int(w * 0.46)
    for ln in lines:
        txt = shape_text(ln, arabic)
        xx = w - int(w * 0.05) - d.textlength(txt, font=fs, **text_kw(arabic)) if arabic else x_text
        d.text((xx, yy), txt, font=fs, fill=INK, **text_kw(arabic))
        yy += fs.size * 1.12
    if tool:
        ft = text_font(int(h * 0.07), bold=True)
        d.text((x_text, yy + h * 0.015), tool, font=ft, fill=ACCENT)
    return im
