"""Panels that show the app under test: a screenshot, the failing sync, the stopwatch, the local-then-cloud flow, the vendor's claim."""

from __future__ import annotations

from PIL import Image, ImageDraw

from cap.render.scenes.draw import app_window, draw_notes, ease, sync_bar
from cap.render.scenes.model import ACCENT, BAD, INK, LINE, MUTED, Scene
from cap.render.text import text_font, wrap_lines


def _panel_media(
    d: ImageDraw.ImageDraw,
    im: Image.Image,
    sc: Scene,
    inner: tuple[int, int, int, int],
    f: float,
    t: float,
    arabic: bool,
) -> bool:
    """A screenshot from the test, when it can be opened; False to fall back."""
    x, y, w, h = inner
    if sc.media is None:
        return False
    try:
        pic = Image.open(sc.media).convert("RGB")
        pic.thumbnail((w, h))
        im.paste(pic, (x + (w - pic.width) // 2, y + (h - pic.height) // 2))
        return True
    except OSError:
        pass
    return False


def _panel_app_fail(
    d: ImageDraw.ImageDraw,
    im: Image.Image,
    sc: Scene,
    inner: tuple[int, int, int, int],
    f: float,
    t: float,
    arabic: bool,
) -> None:
    """The "app_fail" scene."""
    x, y, w, h = inner
    pad = int(h * 0.07)
    area = app_window(d, inner, sc.tool)
    draw_notes(d, area, sc.tool)
    # the bar creeps, then the failure lands (once per "time"), then retries
    phase = (f * (sc.times + 0.5)) % 1.0 if sc.times > 1 else f
    state = "failed" if 0.55 < phase < 0.95 else "syncing"
    sync_bar(d, area, phase * 1.6, state, t, sc.action)
    if sc.day:
        ft = text_font(int(h * 0.07), bold=True)
        d.text(
            (x + w - pad - d.textlength(f"Day {sc.day}", font=ft), y + int(h * 0.16)),
            f"Day {sc.day}",
            font=ft,
            fill=BAD,
        )
    return


def _panel_timer(
    d: ImageDraw.ImageDraw,
    im: Image.Image,
    sc: Scene,
    inner: tuple[int, int, int, int],
    f: float,
    t: float,
    arabic: bool,
) -> None:
    """The "timer" scene."""
    h = inner[3]
    pad = int(h * 0.07)
    area = app_window(d, inner, sc.tool)
    ax, ay, aw, ah = area
    target = sc.number or 0.0
    val = target * ease(f * 1.15)
    # stopwatch ring on the left, the reading beside it, the claim above
    cx, cy, r = ax + aw * 0.22, ay + ah * 0.42, ah * 0.26
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=LINE, width=max(3, int(ah * 0.02)))
    d.arc(
        [cx - r, cy - r, cx + r, cy + r],
        -90,
        -90 + 360 * min(1.0, f * 1.15),
        fill=ACCENT,
        width=max(3, int(ah * 0.02)),
    )
    d.line([(cx, cy), (cx, cy - r * 0.8)], fill=INK, width=max(2, int(ah * 0.012)))
    big = text_font(int(ah * 0.3), bold=True)
    txt = f"{val:.1f}" if target < 100 else f"{val:.0f}"
    d.text((ax + aw * 0.46, ay + ah * 0.2), txt, font=big, fill=INK)
    fu = text_font(int(ah * 0.085))
    d.text((ax + aw * 0.47, ay + ah * 0.55), f"{sc.unit} measured", font=fu, fill=MUTED)
    if sc.claimed is not None:
        fc = text_font(int(ah * 0.07), bold=True)
        msg = f"claimed: within {sc.claimed:g} {sc.unit}"
        mw = d.textlength(msg, font=fc) + pad
        d.rounded_rectangle(
            [
                ax + aw - pad - mw,
                ay + pad * 0.6,
                ax + aw - pad,
                ay + pad * 0.6 + int(ah * 0.12),
            ],
            radius=int(ah * 0.02),
            fill=(0xFF, 0xF3, 0xC4),
        )
        d.text(
            (ax + aw - pad - mw + pad * 0.5, ay + pad * 0.6 + int(ah * 0.025)),
            msg,
            font=fc,
            fill=INK,
        )
        if val > sc.claimed:
            over = f"+{val - sc.claimed:.1f} {sc.unit} over the claim"
            fo = text_font(int(ah * 0.075), bold=True)
            d.text((ax + aw * 0.47, ay + ah * 0.66), over, font=fo, fill=BAD)
    sync_bar(d, area, f, "syncing" if f < 0.98 else "ok", t, sc.action)
    return


def _panel_flow(
    d: ImageDraw.ImageDraw,
    im: Image.Image,
    sc: Scene,
    inner: tuple[int, int, int, int],
    f: float,
    t: float,
    arabic: bool,
) -> None:
    """The "flow" scene."""
    h = inner[3]
    pad = int(h * 0.07)
    area = app_window(d, inner, sc.tool)
    ax, ay, aw, ah = area
    # device (left) -> cloud (right), dots travelling, "local first"
    dx, dy, dw, dh = ax + aw * 0.08, ay + ah * 0.3, aw * 0.26, ah * 0.4
    d.rounded_rectangle(
        [dx, dy, dx + dw, dy + dh],
        radius=int(ah * 0.03),
        fill=(0xF6, 0xF4, 0xEF),
        outline=INK,
        width=max(2, int(ah * 0.008)),
    )
    fl = text_font(int(ah * 0.07), bold=True)
    d.text((dx + aw * 0.02, dy + dh * 0.08), "this device", font=fl, fill=INK)
    for k in range(3):
        d.rectangle(
            [
                dx + dw * 0.1,
                dy + dh * (0.35 + 0.18 * k),
                dx + dw * (0.5 + 0.12 * k),
                dy + dh * (0.4 + 0.18 * k),
            ],
            fill=LINE,
        )
    cx, cy = ax + aw * 0.78, ay + ah * 0.5
    for ox, oy, rr in ((0, 0, 0.17), (-0.09, 0.03, 0.12), (0.1, 0.03, 0.13)):
        r = ah * rr
        d.ellipse(
            [cx + aw * ox - r, cy + ah * oy - r, cx + aw * ox + r, cy + ah * oy + r],
            fill=(0xE4, 0xEC, 0xF2),
            outline=ACCENT,
            width=max(2, int(ah * 0.008)),
        )
    d.text((cx - aw * 0.05, cy - ah * 0.03), "cloud", font=fl, fill=ACCENT)
    x0, x1, yy = dx + dw, cx - aw * 0.2, cy
    d.line([(x0, yy), (x1, yy)], fill=LINE, width=max(2, int(ah * 0.01)))
    for k in range(4):
        p = ((t * 0.35) + k * 0.25) % 1.0
        px = x0 + (x1 - x0) * p
        r = ah * 0.02
        d.ellipse([px - r, yy - r, px + r, yy + r], fill=ACCENT)
    ft = text_font(int(ah * 0.065))
    d.text((dx, dy + dh + ah * 0.06), "1. saved locally first", font=ft, fill=INK)
    d.text((x0 + (x1 - x0) * 0.25, yy - ah * 0.12), "2. then synced", font=ft, fill=MUTED)
    if sc.label:
        fc = text_font(int(ah * 0.07), bold=True)
        mw = d.textlength(sc.label, font=fc) + pad
        d.rounded_rectangle(
            [
                ax + aw - pad - mw,
                ay + pad * 0.6,
                ax + aw - pad,
                ay + pad * 0.6 + int(ah * 0.12),
            ],
            radius=int(ah * 0.02),
            fill=(0xFF, 0xF3, 0xC4),
        )
        d.text(
            (ax + aw - pad - mw + pad * 0.5, ay + pad * 0.6 + int(ah * 0.025)),
            sc.label,
            font=fc,
            fill=INK,
        )
    return


def _panel_claim(
    d: ImageDraw.ImageDraw,
    im: Image.Image,
    sc: Scene,
    inner: tuple[int, int, int, int],
    f: float,
    t: float,
    arabic: bool,
) -> None:
    """The "claim" scene."""
    h = inner[3]
    pad = int(h * 0.07)
    area = app_window(d, inner, sc.tool)
    ax, ay, aw, ah = area
    draw_notes(d, area, sc.tool, rows=3)
    fc = text_font(int(ah * 0.075), bold=True)
    lines = wrap_lines(d, f"\u201c{sc.label}\u201d", fc, aw - 2 * pad - int(ah * 0.1))
    bh = int(len(lines) * fc.size * 1.3 + pad)
    d.rounded_rectangle(
        [
            ax + pad,
            ay + ah - pad - bh - int(ah * 0.02),
            ax + aw - pad,
            ay + ah - pad - int(ah * 0.02),
        ],
        radius=int(ah * 0.02),
        fill=(0xFF, 0xF3, 0xC4),
    )
    yy = ay + ah - pad - bh + pad * 0.4 - int(ah * 0.02)
    for ln in lines:
        d.text((ax + pad * 1.5, yy), ln, font=fc, fill=INK)
        yy += fc.size * 1.3
    ft = text_font(int(ah * 0.055))
    d.text(
        (ax + pad, ay + ah - pad - bh - int(ah * 0.02) - ft.size * 1.4),
        "the vendor's claim, from the help page",
        font=ft,
        fill=MUTED,
    )
    return
