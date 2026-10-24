"""Drawing helpers: the layout, the chrome around the panel, the reconstructed app window and the subtitle."""

from __future__ import annotations

from dataclasses import dataclass

from PIL import ImageDraw

from cap.render.scenes.model import ACCENT, BAD, BEZEL, INK, LINE, MUTED, OK, SURFACE
from cap.render.text import shape_text, text_font, text_kw, wrap_lines


def ease(f: float) -> float:
    f = max(0.0, min(1.0, f))
    return 1 - (1 - f) * (1 - f)


@dataclass
class Layout:
    size: tuple[int, int]
    portrait: bool
    char_box: tuple[int, int, int, int]
    panel: tuple[int, int, int, int]
    subtitle: tuple[int, int, int, int]
    margin: int


def layout(size: tuple[int, int]) -> Layout:
    w, h = size
    m = int(min(w, h) * 0.05)
    if w >= h:
        top = int(h * 0.12)
        return Layout(
            size,
            False,
            (m, top, int(w * 0.25), int(h * 0.67)),
            (int(w * 0.33), top, w - m - int(w * 0.33), int(h * 0.66)),
            (m, int(h * 0.82), w - 2 * m, int(h * 0.13)),
            m,
        )
    top = int(h * 0.09)
    return Layout(
        size,
        True,
        (m, int(h * 0.56), int(w * 0.42), int(h * 0.36)),
        (m, top, w - 2 * m, int(h * 0.44)),
        (int(w * 0.48), int(h * 0.58), w - int(w * 0.48) - m, int(h * 0.32)),
        m,
    )


def draw_chrome(d: ImageDraw.ImageDraw, lay: Layout, character: str, channel: str) -> None:
    from cap.policy import AI_ROLE_DISCLOSURE

    w, h = lay.size
    m = lay.margin
    s = int(min(w, h) * 0.026)
    d.text(
        (m, m * 0.7), f"{channel} · {character}".upper(), font=text_font(s, bold=True), fill=ACCENT
    )
    label = AI_ROLE_DISCLOSURE.rstrip(".")
    f = text_font(int(s * 0.9))
    tw = d.textlength(label, font=f)
    d.text((w - m - tw, m * 0.7), label, font=f, fill=MUTED)


def panel_frame(d: ImageDraw.ImageDraw, lay: Layout) -> tuple[int, int, int, int]:
    """A device frame; returns the inner screen box."""
    x, y, w, h = lay.panel
    r = int(min(w, h) * 0.04)
    d.rounded_rectangle([x, y, x + w, y + h], radius=r, fill=BEZEL)
    b = int(min(w, h) * 0.025)
    inner = (x + b, y + b, w - 2 * b, h - 2 * b)
    d.rounded_rectangle(
        [inner[0], inner[1], inner[0] + inner[2], inner[1] + inner[3]],
        radius=int(r * 0.6),
        fill=SURFACE,
    )
    return inner


def app_window(
    d: ImageDraw.ImageDraw, inner: tuple[int, int, int, int], tool: str
) -> tuple[int, int, int, int]:
    """A generic app window with a title bar; returns the content area."""
    x, y, w, h = inner
    bar = int(h * 0.11)
    d.rectangle([x, y, x + w, y + bar], fill=(0xEE, 0xEC, 0xE6))
    for i, c in enumerate(((0xE0, 0x5A, 0x4E), (0xE8, 0xB9, 0x2E), (0x2E, 0x9E, 0x5B))):
        cx = x + bar * 0.6 + i * bar * 0.55
        d.ellipse([cx - bar * 0.16, y + bar * 0.34, cx + bar * 0.16, y + bar * 0.66], fill=c)
    f = text_font(int(bar * 0.42), bold=True)
    d.text((x + bar * 2.4, y + bar * 0.27), tool, font=f, fill=INK)
    tag = "reconstruction of the logged event"
    ft = text_font(int(bar * 0.3))
    d.text(
        (x + w - d.textlength(tag, font=ft) - bar * 0.5, y + bar * 0.33), tag, font=ft, fill=MUTED
    )
    return (x, y + bar, w, h - bar)


def draw_notes(
    d: ImageDraw.ImageDraw, area: tuple[int, int, int, int], tool: str, rows: int = 4
) -> None:
    x, y, w, h = area
    pad = int(h * 0.06)
    fh = text_font(int(h * 0.07), bold=True)
    d.text((x + pad, y + pad), "Notes", font=fh, fill=INK)
    yy = y + pad + int(h * 0.14)
    for i in range(rows):
        d.rounded_rectangle(
            [x + pad, yy, x + w - pad, yy + int(h * 0.12)],
            radius=int(h * 0.02),
            fill=(0xF6, 0xF4, 0xEF),
        )
        d.rectangle(
            [
                x + pad * 2,
                yy + int(h * 0.04),
                x + pad * 2 + int(w * (0.3 + 0.1 * (i % 3))),
                yy + int(h * 0.06),
            ],
            fill=LINE,
        )
        yy += int(h * 0.15)


_VERBS = {
    "Sync": "Syncing…",
    "Export": "Exporting…",
    "Upload": "Uploading…",
    "Transcript": "Transcribing…",
    "Import": "Importing…",
    "Save": "Saving…",
    "Sign-in": "Signing in…",
    "Render": "Rendering…",
    "Search": "Searching…",
}


def sync_bar(
    d: ImageDraw.ImageDraw,
    area: tuple[int, int, int, int],
    progress: float,
    state: str,
    t: float,
    action: str = "Sync",
) -> None:
    """A sync status row at the bottom of the app: syncing (spinner + bar), ok, failed."""
    x, y, w, h = area
    pad = int(h * 0.06)
    by = y + h - int(h * 0.2)
    d.rectangle([x, by - pad, x + w, y + h], fill=(0xF1, 0xEF, 0xE9))
    f = text_font(int(h * 0.065), bold=True)
    if state == "failed":
        d.rounded_rectangle(
            [x + pad, by, x + w - pad, by + int(h * 0.12)], radius=int(h * 0.02), fill=BAD
        )
        d.text(
            (x + pad * 2, by + int(h * 0.025)), f"{action} failed · retrying", font=f, fill=SURFACE
        )
        return
    if state == "ok":
        d.text((x + pad, by + int(h * 0.02)), f"{action} done", font=f, fill=OK)
        return
    # syncing: spinner and a bar that stalls
    cx, cy, r = x + pad + int(h * 0.05), by + int(h * 0.06), int(h * 0.04)
    a0 = (t * 300) % 360
    d.arc([cx - r, cy - r, cx + r, cy + r], a0, a0 + 250, fill=ACCENT, width=max(2, int(h * 0.012)))
    verb = _VERBS.get(action, "Working…")
    d.text((cx + r * 2, by + int(h * 0.02)), verb, font=f, fill=INK)
    bx0, bx1 = x + pad, x + w - pad
    d.rectangle([bx0, by + int(h * 0.14), bx1, by + int(h * 0.16)], fill=LINE)
    d.rectangle(
        [bx0, by + int(h * 0.14), bx0 + (bx1 - bx0) * min(0.92, progress), by + int(h * 0.16)],
        fill=ACCENT,
    )


def draw_subtitle(d: ImageDraw.ImageDraw, lay: Layout, sentence: str, arabic: bool) -> None:
    x, y, w, h = lay.subtitle
    size = int(min(lay.size) * (0.036 if not lay.portrait else 0.034))
    f = text_font(size, arabic=arabic)
    lines = wrap_lines(d, sentence, f, w, arabic)[: 4 if lay.portrait else 3]
    lh = int(size * 1.35)
    yy = y
    for ln in lines:
        txt = shape_text(ln, arabic)
        xx = x + w - d.textlength(txt, font=f, **text_kw(arabic)) if arabic else x
        d.text((xx, yy), txt, font=f, fill=INK, **text_kw(arabic))
        yy += lh
