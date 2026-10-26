"""Text on a frame: the fonts, Arabic shaping and right-to-left layout, line wrapping.
The slide renderer and the scenes both draw with these."""

from __future__ import annotations

from pathlib import Path

from PIL import ImageDraw, ImageFont

FONTS = Path(__file__).resolve().parent / "fonts"


def text_font(size: int, bold: bool = False, arabic: bool = False) -> ImageFont.FreeTypeFont:
    name = ("IBMPlexSansArabic" if arabic else "IBMPlexSans") + (
        "-SemiBold" if bold else "-Regular"
    )
    return ImageFont.truetype(str(FONTS / f"{name}.ttf"), size)


def raqm_available() -> bool:
    from PIL import features

    return bool(features.check("raqm"))


def shape_text(text: str, arabic: bool) -> str:
    """Pillow with libraqm shapes Arabic itself (direction='rtl' below); without it,
    pre-shape with arabic_reshaper and reorder with python-bidi. Never both."""
    if not arabic or raqm_available():
        return text
    import arabic_reshaper
    from bidi.algorithm import get_display

    return str(get_display(arabic_reshaper.reshape(text)))


def text_kw(arabic: bool) -> dict:
    return {"direction": "rtl", "language": "ar"} if arabic and raqm_available() else {}


def wrap_lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    width: int,
    arabic: bool = False,
) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(shape_text(trial, arabic), font=font, **text_kw(arabic)) <= width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines
