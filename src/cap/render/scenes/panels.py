"""The panel for each scene kind, chosen by kind: orbit, figure, steps and the default; the others are in panels_app and panels_tally."""

from __future__ import annotations

from PIL import Image, ImageDraw

from cap.render.scenes.draw import ease
from cap.render.scenes.model import ACCENT, EARTH, INK, LAND, LINE, MUTED, SKY, SURFACE, Scene
from cap.render.scenes.panels_app import (
    _panel_app_fail,
    _panel_claim,
    _panel_flow,
    _panel_media,
    _panel_timer,
)
from cap.render.scenes.panels_tally import (
    _panel_calendar,
    _panel_ratio,
    _panel_task,
    _panel_verdict,
)
from cap.render.text import shape_text, text_font, text_kw, wrap_lines


def _panel_orbit(
    d: ImageDraw.ImageDraw,
    im: Image.Image,
    sc: Scene,
    inner: tuple[int, int, int, int],
    f: float,
    t: float,
    arabic: bool,
) -> None:
    """The "orbit" scene."""
    x, y, w, h = inner
    pad = int(h * 0.07)
    d.rectangle([x, y, x + w, y + h], fill=SKY)
    for k in range(40):  # stars
        sx = x + ((k * 97) % w)
        sy = y + ((k * 61) % int(h * 0.6))
        d.point((sx, sy), fill=(0xC0, 0xC8, 0xD0))
    r = w * 0.9
    cx, cy = x + w / 2, y + h * 0.55 + r
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=EARTH)
    for ox, oy, rw, rh in (
        (-0.2, 0.08, 0.18, 0.09),
        (0.1, 0.12, 0.25, 0.1),
        (0.35, 0.2, 0.15, 0.07),
    ):
        d.ellipse(
            [cx + w * ox, y + h * (0.6 + oy) + 0, cx + w * (ox + rw), y + h * (0.6 + oy + rh)],
            fill=LAND,
        )
    d.arc(
        [cx - r - 6, cy - r - 6, cx + r + 6, cy + r + 6],
        200,
        340,
        fill=(0x9F, 0xD0, 0xF0),
        width=6,
    )
    zoom = ease(f)
    bx = x + w * (0.25 + 0.4 * zoom)
    by = y + h * (0.62 + 0.1 * zoom)
    br = h * (0.015 + 0.04 * zoom)
    d.ellipse([bx - br, by - br, bx + br, by + br], outline=(0xFF, 0xD4, 0x6A), width=3)
    fl = text_font(int(h * 0.07), bold=True)
    d.text((x + pad, y + pad), "from orbit", font=fl, fill=(0xE8, 0xEE, 0xF4))
    return


def _panel_figure(
    d: ImageDraw.ImageDraw,
    im: Image.Image,
    sc: Scene,
    inner: tuple[int, int, int, int],
    f: float,
    t: float,
    arabic: bool,
) -> None:
    """The "figure" scene."""
    x, y, w, h = inner
    pad = int(h * 0.07)
    big = text_font(int(h * 0.36), bold=True)
    val = (sc.number or 0) * ease(f * 1.2)
    txt = f"{val:,.0f}" if (sc.number or 0) >= 100 else f"{val:.1f}"
    tw = d.textlength(txt, font=big)
    d.text((x + w / 2 - tw / 2, y + h * 0.2), txt, font=big, fill=INK)
    fu = text_font(int(h * 0.09))
    d.text(
        (x + w / 2 - d.textlength(sc.unit, font=fu) / 2, y + h * 0.62),
        sc.unit,
        font=fu,
        fill=MUTED,
    )
    ft = text_font(int(h * 0.055))
    d.text((x + pad, y + h - pad - ft.size), "from the logbook, source cited", font=ft, fill=ACCENT)
    return


def _panel_steps(
    d: ImageDraw.ImageDraw,
    im: Image.Image,
    sc: Scene,
    inner: tuple[int, int, int, int],
    f: float,
    t: float,
    arabic: bool,
) -> None:
    """The "steps" scene."""
    x, y, w, h = inner
    pad = int(h * 0.07)
    fs = text_font(int(h * 0.075), bold=True, arabic=arabic)
    yy = y + pad
    shown = int(len(sc.items) * min(1.0, f * 1.3 + 0.3))
    for k, item in enumerate(sc.items):
        on = k < shown
        d.rounded_rectangle(
            [x + pad, yy, x + w - pad, yy + int(h * 0.17)],
            radius=int(h * 0.03),
            fill=(0xF6, 0xF4, 0xEF) if on else SURFACE,
            outline=LINE,
        )
        txt = shape_text(f"{k + 1}. {item}", arabic)
        d.text(
            (x + pad * 2, yy + int(h * 0.045)),
            txt,
            font=fs,
            fill=INK if on else LINE,
            **text_kw(arabic),
        )
        yy += int(h * 0.21)
    return


def _panel_talk(
    d: ImageDraw.ImageDraw,
    im: Image.Image,
    sc: Scene,
    inner: tuple[int, int, int, int],
    f: float,
    t: float,
    arabic: bool,
) -> None:
    """The default scene: the key sentence large, the character does the work."""
    x, y, w, h = inner
    pad = int(h * 0.07)
    # talk: the key sentence large on the panel, the character does the work
    fs = text_font(int(h * (0.1 if len(sc.sentences[0]) < 60 else 0.08)), bold=True, arabic=arabic)
    key = sc.sentences[0] if sc.sentences else sc.text
    lines = wrap_lines(d, key, fs, w - 2 * pad, arabic)[:5]
    yy = y + h / 2 - len(lines) * fs.size * 1.25 / 2
    for ln in lines:
        txt = shape_text(ln, arabic)
        xx = x + w - pad - d.textlength(txt, font=fs, **text_kw(arabic)) if arabic else x + pad
        d.text((xx, yy), txt, font=fs, fill=INK, **text_kw(arabic))
        yy += fs.size * 1.25


_PANELS = {
    "app_fail": _panel_app_fail,
    "timer": _panel_timer,
    "flow": _panel_flow,
    "calendar": _panel_calendar,
    "verdict": _panel_verdict,
    "orbit": _panel_orbit,
    "figure": _panel_figure,
    "steps": _panel_steps,
    "task": _panel_task,
    "ratio": _panel_ratio,
    "claim": _panel_claim,
}


def draw_panel(
    d: ImageDraw.ImageDraw,
    im: Image.Image,
    sc: Scene,
    inner: tuple[int, int, int, int],
    f: float,
    t: float,
    arabic: bool,
) -> None:
    """Draw the scene's panel: its own drawing for each kind, the key sentence otherwise."""
    if (
        sc.kind == "media"
        and sc.media
        and sc.media.exists()
        and _panel_media(d, im, sc, inner, f, t, arabic)
    ):
        return
    _PANELS.get(sc.kind, _panel_talk)(d, im, sc, inner, f, t, arabic)


def sources_panel(
    d: ImageDraw.ImageDraw, inner: tuple[int, int, int, int], sources: list[str]
) -> None:
    x, y, w, h = inner
    pad = int(h * 0.07)
    d.text((x + pad, y + pad), "Sources", font=text_font(int(h * 0.1), bold=True), fill=INK)
    f = text_font(int(h * 0.055))
    yy = y + pad + int(h * 0.16)
    for s in sources[:6]:
        for ln in wrap_lines(d, s, f, w - 2 * pad)[:2]:
            d.text((x + pad, yy), ln, font=f, fill=MUTED)
            yy += int(f.size * 1.4)
        yy += int(f.size * 0.4)
    if not sources:
        d.text((x + pad, yy), "the presenter's own test log", font=f, fill=MUTED)
