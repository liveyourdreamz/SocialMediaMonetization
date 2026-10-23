"""Panels that keep score: the seven-day strip, the verdict stamp, the task checklist, the ratio."""

from __future__ import annotations

import math

from PIL import Image, ImageDraw

from cap.render.scenes.draw import app_window, ease
from cap.render.scenes.model import ACCENT, BAD, INK, LINE, MUTED, OK, SURFACE, WARN, Scene
from cap.render.text import text_font


def _panel_calendar(
    d: ImageDraw.ImageDraw,
    im: Image.Image,
    sc: Scene,
    inner: tuple[int, int, int, int],
    f: float,
    t: float,
    arabic: bool,
) -> None:
    """The "calendar" scene."""
    h = inner[3]
    pad = int(h * 0.07)
    area = app_window(d, inner, sc.tool)
    ax, ay, aw, ah = area
    n = max(1, min(sc.days, 14))
    gap = int(aw * 0.015)
    cw = (aw - pad * 2 - gap * (n - 1)) / n
    lit = int(n * min(1.0, f * 1.2))
    fd = text_font(int(cw * 0.26), bold=True)
    for k in range(n):
        cx0 = ax + pad + k * (cw + gap)
        on = k < lit
        d.rounded_rectangle(
            [cx0, ay + ah * 0.25, cx0 + cw, ay + ah * 0.75],
            radius=int(cw * 0.12),
            fill=ACCENT if on else (0xF1, 0xEF, 0xE9),
            outline=LINE,
        )
        lbl = f"{k + 1}"
        d.text(
            (cx0 + cw / 2 - d.textlength(lbl, font=fd) / 2, ay + ah * 0.42),
            lbl,
            font=fd,
            fill=SURFACE if on else MUTED,
        )
    ft = text_font(int(ah * 0.08), bold=True)
    d.text((ax + pad, ay + ah * 0.1), f"{sc.days} days of logged tests", font=ft, fill=INK)
    return


def _panel_verdict(
    d: ImageDraw.ImageDraw,
    im: Image.Image,
    sc: Scene,
    inner: tuple[int, int, int, int],
    f: float,
    t: float,
    arabic: bool,
) -> None:
    """The "verdict" scene."""
    h = inner[3]
    pad = int(h * 0.07)
    area = app_window(d, inner, sc.tool)
    ax, ay, aw, ah = area
    word = {"keep": "KEEP", "skip": "SKIP", "watch": "WATCH"}[sc.mood]
    colour = {"keep": OK, "skip": BAD, "watch": WARN}[sc.mood]
    s = ease(f * 2.2)
    fs = text_font(int(ah * 0.3 * (0.6 + 0.4 * s)), bold=True)
    tw = d.textlength(word, font=fs)
    cx, cy = ax + aw / 2, ay + ah * 0.42
    d.rounded_rectangle(
        [cx - tw / 2 - pad, cy - fs.size * 0.65, cx + tw / 2 + pad, cy + fs.size * 0.65],
        radius=int(ah * 0.03),
        outline=colour,
        width=max(4, int(ah * 0.02)),
    )
    d.text((cx - tw / 2, cy - fs.size * 0.62), word, font=fs, fill=colour)
    ft = text_font(int(ah * 0.07))
    sub = "from the test log"
    d.text((cx - d.textlength(sub, font=ft) / 2, cy + fs.size * 0.8), sub, font=ft, fill=MUTED)
    return


def _panel_task(
    d: ImageDraw.ImageDraw,
    im: Image.Image,
    sc: Scene,
    inner: tuple[int, int, int, int],
    f: float,
    t: float,
    arabic: bool,
) -> None:
    """The "task" scene."""
    h = inner[3]
    pad = int(h * 0.07)
    area = app_window(d, inner, sc.tool)
    ax, ay, aw, ah = area
    fh = text_font(int(ah * 0.085), bold=True)
    d.text((ax + pad, ay + pad * 0.8), "The test: three tasks", font=fh, fill=INK)
    items = sc.items or ["task one", "task two", "task three"]
    fi = text_font(int(ah * 0.075))
    yy = ay + pad * 0.8 + int(ah * 0.16)
    for k, item in enumerate(items, start=1):
        done, now = k < sc.index, k == sc.index
        box = int(ah * 0.075)
        d.rounded_rectangle(
            [ax + pad, yy, ax + pad + box, yy + box],
            radius=int(box * 0.2),
            fill=OK if done else (ACCENT if now else SURFACE),
            outline=LINE if not (done or now) else None,
            width=2,
        )
        if done:
            d.line(
                [
                    (ax + pad + box * 0.25, yy + box * 0.55),
                    (ax + pad + box * 0.45, yy + box * 0.75),
                    (ax + pad + box * 0.78, yy + box * 0.3),
                ],
                fill=SURFACE,
                width=max(2, int(box * 0.12)),
            )
        if now:
            pulse = 0.5 + 0.5 * math.sin(t * 4)
            d.ellipse(
                [
                    ax + pad + box * (0.5 - 0.18 * pulse),
                    yy + box * (0.5 - 0.18 * pulse),
                    ax + pad + box * (0.5 + 0.18 * pulse),
                    yy + box * (0.5 + 0.18 * pulse),
                ],
                fill=SURFACE,
            )
        d.text(
            (ax + pad + box + pad * 0.6, yy + box * 0.1),
            f"Task {k}: {item}",
            font=fi,
            fill=INK if (done or now) else MUTED,
        )
        yy += int(ah * 0.14)
    return


def _panel_ratio(
    d: ImageDraw.ImageDraw,
    im: Image.Image,
    sc: Scene,
    inner: tuple[int, int, int, int],
    f: float,
    t: float,
    arabic: bool,
) -> None:
    """The "ratio" scene."""
    h = inner[3]
    pad = int(h * 0.07)
    area = app_window(d, inner, sc.tool)
    ax, ay, aw, ah = area
    found, total = sc.number or 0.0, max(1, sc.total)
    prog = ease(f * 1.3)
    fh = text_font(int(ah * 0.085), bold=True)
    d.text((ax + pad, ay + pad * 0.8), "Action items", font=fh, fill=INK)
    # a row of boxes: found (teal), missed (hollow), invented (red, struck)
    n_show = total + int(sc.extra or 0)
    bw = min(int(ah * 0.13), int((aw - 2 * pad) / max(1, n_show) * 0.8))
    gap = int(bw * 0.25)
    yy = ay + ah * 0.33
    lit = int(round(n_show * prog))
    for k in range(n_show):
        x0 = ax + pad + k * (bw + gap)
        on = k < lit
        if k < found:
            d.rounded_rectangle(
                [x0, yy, x0 + bw, yy + bw],
                radius=int(bw * 0.2),
                fill=OK if on else (0xF1, 0xEF, 0xE9),
            )
        elif k < total:
            d.rounded_rectangle(
                [x0, yy, x0 + bw, yy + bw],
                radius=int(bw * 0.2),
                outline=MUTED if on else LINE,
                width=2,
            )
        else:
            d.rounded_rectangle(
                [x0, yy, x0 + bw, yy + bw],
                radius=int(bw * 0.2),
                fill=BAD if on else (0xF1, 0xEF, 0xE9),
            )
            if on:
                d.line(
                    [(x0 + bw * 0.25, yy + bw * 0.25), (x0 + bw * 0.75, yy + bw * 0.75)],
                    fill=SURFACE,
                    width=3,
                )
                d.line(
                    [(x0 + bw * 0.75, yy + bw * 0.25), (x0 + bw * 0.25, yy + bw * 0.75)],
                    fill=SURFACE,
                    width=3,
                )
    big = text_font(int(ah * 0.16), bold=True)
    d.text((ax + pad, yy + bw + ah * 0.08), f"{found:g} of {total}", font=big, fill=INK)
    fs = text_font(int(ah * 0.07))
    d.text(
        (ax + pad, yy + bw + ah * 0.26),
        "found, of the items written down by hand",
        font=fs,
        fill=MUTED,
    )
    if sc.extra:
        fb = text_font(int(ah * 0.09), bold=True)
        msg = f"{sc.extra:g} invented"
        d.text(
            (ax + aw - pad - d.textlength(msg, font=fb), yy + bw + ah * 0.1),
            msg,
            font=fb,
            fill=BAD,
        )
    return
