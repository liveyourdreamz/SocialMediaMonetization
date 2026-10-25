"""The photo scene: a real photograph filling the frame with a slow pan and zoom (the
"Ken Burns" move), the character small in the corner, the sentence in large white type
on a dark fade, the credit and the AI-character label always on screen (R-16).

Motion is subpixel (a float crop box), so it stays smooth at 15 frames a second.
Alternate scenes zoom in and out so consecutive photos do not move the same way.
"""

from __future__ import annotations

from functools import lru_cache

from PIL import Image, ImageDraw

from cap.render.characters import draw_falak, draw_patch
from cap.render.scenes.draw import Layout, ease
from cap.render.scenes.model import Scene
from cap.render.text import shape_text, text_font, text_kw, wrap_lines

OVERSCAN = 1.18  # the photo is held this much larger than the frame: room to move
WHITE, SHADOW = (0xFF, 0xFF, 0xFF), (0, 0, 0)


@lru_cache(maxsize=6)
def _base(path: str, w: int, h: int) -> Image.Image:
    """The photo, scaled once so it covers the frame times OVERSCAN."""
    im = Image.open(path).convert("RGB")
    s = max(w * OVERSCAN / im.width, h * OVERSCAN / im.height)
    return im.resize(
        (max(1, round(im.width * s)), max(1, round(im.height * s))), Image.Resampling.LANCZOS
    )


@lru_cache(maxsize=4)
def _shade(w: int, h: int, portrait: bool) -> Image.Image:
    """A mask: dark at the top (the labels) and towards the bottom (the sentence)."""
    m = Image.new("L", (1, h))
    top, low = int(h * 0.16), int(h * (0.48 if portrait else 0.55))
    for y in range(h):
        a = 150 * (1 - y / top) if y < top else 0
        if y > low:
            a = max(a, 205 * min(1.0, (y - low) / (h * 0.25)))
        m.putpixel((0, y), int(a))
    return m.resize((w, h))


def _move(base: Image.Image, size: tuple[int, int], f: float, inward: bool) -> Image.Image:
    w, h = size
    z = 1.0 + (OVERSCAN - 1.0) * (ease(f) if inward else 1 - ease(f))  # 1 .. OVERSCAN
    ww, wh = w * OVERSCAN / z, h * OVERSCAN / z
    px = 0.35 + 0.3 * (f if inward else 1 - f)  # a gentle drift across
    x0 = (base.width - ww) * px
    y0 = (base.height - wh) * 0.45
    return base.resize((w, h), Image.Resampling.BILINEAR, box=(x0, y0, x0 + ww, y0 + wh))


def _label(
    d: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, size: int, bold: bool
) -> None:
    d.text(xy, text, font=text_font(size, bold=bold), fill=WHITE,
           stroke_width=max(1, size // 14), stroke_fill=SHADOW)  # fmt: skip


def _chrome(d: ImageDraw.ImageDraw, lay: Layout, character: str, channel: str, credit: str) -> None:
    from cap.policy import AI_ROLE_DISCLOSURE

    w, h = lay.size
    m, s = lay.margin, int(min(w, h) * 0.026)
    _label(d, (m, m * 0.7), f"{channel} · {character}".upper(), s, True)
    label = AI_ROLE_DISCLOSURE.rstrip(".")
    tw = d.textlength(label, font=text_font(int(s * 0.9)))
    _label(d, (w - m - tw, m * 0.7), label, int(s * 0.9), False)
    if credit:
        fc = int(s * 0.62)
        text = f"Photo: {credit}"
        while len(text) > 20 and d.textlength(text, font=text_font(fc)) > w - 2 * m:
            text = text[:-2]
        _label(d, (m, h - m * 0.45 - fc), text, fc, False)


def _boxes(lay: Layout) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
    """(character box, sentence box) for this frame shape. Falak's solar panels reach
    well past its box, so the sentence starts clear of them."""
    w, h = lay.size
    m = lay.margin
    if lay.portrait:  # above the platform's own buttons and captions (bottom ~22%)
        return (m, int(h * 0.60), int(w * 0.26), int(h * 0.15)), (
            int(w * 0.38), int(h * 0.60), w - int(w * 0.38) - m, int(h * 0.16),
        )  # fmt: skip
    return (m, int(h * 0.58), int(w * 0.14), int(h * 0.33)), (
        int(w * 0.22), int(h * 0.70), w - int(w * 0.22) - m, int(h * 0.22),
    )  # fmt: skip


def _sentence(d: ImageDraw.ImageDraw, lay: Layout, box: tuple[int, int, int, int], text: str,
              arabic: bool) -> None:  # fmt: skip
    x, y, w, h = box
    size = int(min(lay.size) * (0.042 if lay.portrait else 0.048))
    while True:
        f = text_font(size, bold=True, arabic=arabic)
        lines = wrap_lines(d, text, f, w, arabic)
        if len(lines) * size * 1.25 <= h or size <= 22:
            break
        size -= 2
    yy = y + max(0, (h - len(lines) * size * 1.25) / 2)
    for ln in lines[:4]:
        txt = shape_text(ln, arabic)
        xx = x + w - d.textlength(txt, font=f, **text_kw(arabic)) if arabic else x
        d.text((xx, yy), txt, font=f, fill=WHITE, stroke_width=max(2, size // 16),
               stroke_fill=SHADOW, **text_kw(arabic))  # fmt: skip
        yy += size * 1.25


def photo_frame(
    sc: Scene, i: int, n: int, t_total: float, lay: Layout, *,
    character: str, channel: str, level: float, arabic: bool,
) -> Image.Image:  # fmt: skip
    assert sc.photo is not None
    w, h = lay.size
    f = i / max(1, n - 1)
    im = _move(_base(str(sc.photo), w, h), (w, h), f, inward=sc.index % 2 == 0)
    im.paste((0, 0, 0), (0, 0, w, h), _shade(w, h, lay.portrait))
    d = ImageDraw.Draw(im)
    _chrome(d, lay, character, channel, sc.credit)
    char_box, text_box = _boxes(lay)
    mood = sc.mood if f > 0.15 else "idle"
    if character == "Falak":
        draw_falak(im, char_box, t_total, level, mood)
    else:
        draw_patch(im, char_box, t_total, level, mood)
    if sc.sentences:
        k = min(len(sc.sentences) - 1, int(f * len(sc.sentences)))
        _sentence(d, lay, text_box, sc.sentences[k], arabic)
    return im
