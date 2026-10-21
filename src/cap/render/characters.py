"""The two presenters, drawn from their Anchor Registry profiles (PRD Appendix B) as
procedural vector shapes: original designs, no faces with big eyes, nothing that
resembles an existing film or game character.

Patch: a retired server-room repair robot. Compact dome-headed body on a magnetic
base (no treads), one small camera-lens eye, one oversized repair arm holding a
multimeter, scuffed paint, an asset tag reading P-7, a chest status light that shows
the verdict. The lens glows with the voice, so Patch visibly speaks.

Falak: an old survey satellite. Small boxy body, two long solar panels, worn and
patched gold-foil insulation, one dish antenna, a small blinking beacon. Expression
comes only through panel angle and beacon rhythm: no face.

Every function draws into a box (x, y, w, h) on a PIL image at time t (seconds) with
`level` (0..1) the current loudness of the voice and `mood` one of
idle/keep/skip/watch/measure.
"""

from __future__ import annotations

import math

from PIL import Image, ImageDraw

INK = (0x1B, 0x1A, 0x17)
STEEL = (0xB9, 0xBC, 0xC1)
STEEL_DARK = (0x7E, 0x82, 0x88)
STEEL_LIGHT = (0xE3, 0xE5, 0xE8)
SCUFF = (0x9A, 0x8F, 0x7E)
LENS = (0x1F, 0x5F, 0x73)
LENS_GLOW = (0x8F, 0xD6, 0xEA)
GOLD = (0xC9, 0xA2, 0x3E)
GOLD_DARK = (0x8F, 0x6E, 0x1E)
PANEL = (0x1E, 0x3A, 0x5F)
PANEL_LINE = (0x4F, 0x7A, 0xB0)
LIGHT = {
    "idle": (0x9A, 0x9A, 0x9A),
    "measure": (0xE8, 0xB9, 0x2E),
    "keep": (0x2E, 0x9E, 0x5B),
    "watch": (0xE8, 0xB9, 0x2E),
    "skip": (0xC8, 0x3C, 0x3C),
}


def _lerp(a: tuple[int, int, int], b: tuple[int, int, int], f: float) -> tuple[int, int, int]:
    f = max(0.0, min(1.0, f))
    return tuple(int(a[i] + (b[i] - a[i]) * f) for i in range(3))  # type: ignore[return-value]


def draw_patch(
    im: Image.Image,
    box: tuple[int, int, int, int],
    t: float,
    level: float = 0.0,
    mood: str = "idle",
    reading: str = "--.-",
) -> None:
    """Patch, standing on the bottom edge of the box, facing right (towards the screen)."""
    d = ImageDraw.Draw(im)
    x, y, w, h = box
    u = h / 100.0  # one unit = 1% of the box height
    cx = x + w * 0.4
    bob = math.sin(t * 2.1) * 0.8 * u
    floor = y + h

    # magnetic base: a flat disc with a shadow, slight hover
    bw, bh = 46 * u, 9 * u
    d.ellipse(
        [cx - bw / 2 - 2 * u, floor - bh * 0.6, cx + bw / 2 + 2 * u, floor + bh * 0.4],
        fill=(0, 0, 0, 0) if False else (0xD9, 0xD6, 0xCE),
    )
    d.ellipse(
        [cx - bw / 2, floor - bh - bob * 0.3, cx + bw / 2, floor - bob * 0.3], fill=STEEL_DARK
    )
    d.ellipse(
        [
            cx - bw / 2 + 2 * u,
            floor - bh - bob * 0.3 + 1.5 * u,
            cx + bw / 2 - 2 * u,
            floor - bob * 0.3 - 1.5 * u,
        ],
        fill=STEEL,
    )

    # body: compact rounded block
    body_w, body_h = 40 * u, 38 * u
    bx0, by0 = cx - body_w / 2, floor - bh - body_h + bob
    d.rounded_rectangle(
        [bx0, by0, bx0 + body_w, by0 + body_h],
        radius=int(6 * u),
        fill=STEEL,
        outline=STEEL_DARK,
        width=max(1, int(0.8 * u)),
    )
    # scuffs
    for sx, sy, sl in ((0.15, 0.7, 0.18), (0.6, 0.25, 0.12), (0.7, 0.8, 0.1)):
        d.line(
            [
                (bx0 + body_w * sx, by0 + body_h * sy),
                (bx0 + body_w * (sx + sl), by0 + body_h * (sy + 0.04)),
            ],
            fill=SCUFF,
            width=max(1, int(0.9 * u)),
        )
    # asset tag P-7
    tag_w, tag_h = 13 * u, 6 * u
    tx, ty = bx0 + body_w * 0.58, by0 + body_h * 0.62
    d.rectangle([tx, ty, tx + tag_w, ty + tag_h], fill=STEEL_LIGHT, outline=STEEL_DARK, width=1)
    _text(d, (tx + 1.5 * u, ty + 0.6 * u), "P-7", int(4.2 * u), INK, bold=True)
    # chest status light
    lx, ly, lr = bx0 + body_w * 0.28, by0 + body_h * 0.3, 4.2 * u
    colour = LIGHT.get(mood, LIGHT["idle"])
    pulse = 0.5 + 0.5 * math.sin(t * 3.0) if mood != "idle" else 0.0
    d.ellipse(
        [lx - lr - 1.2 * u, ly - lr - 1.2 * u, lx + lr + 1.2 * u, ly + lr + 1.2 * u],
        fill=STEEL_DARK,
    )
    d.ellipse(
        [lx - lr, ly - lr, lx + lr, ly + lr], fill=_lerp(colour, (255, 255, 255), 0.25 * pulse)
    )

    # neck and dome head
    nw, nh = 12 * u, 5 * u
    d.rectangle([cx - nw / 2, by0 - nh, cx + nw / 2, by0 + 1], fill=STEEL_DARK)
    hr = 19 * u
    hy = by0 - nh
    d.pieslice(
        [cx - hr, hy - hr, cx + hr, hy + hr],
        180,
        360,
        fill=STEEL,
        outline=STEEL_DARK,
        width=max(1, int(0.8 * u)),
    )
    d.line([(cx - hr, hy), (cx + hr, hy)], fill=STEEL_DARK, width=max(1, int(0.8 * u)))
    # one small camera-lens eye, looking right; brightens with the voice; blinks
    blink = (t % 3.7) < 0.12
    ex, ey, er = cx + hr * 0.35, hy - hr * 0.45, 4.6 * u
    d.ellipse(
        [ex - er - 1.5 * u, ey - er - 1.5 * u, ex + er + 1.5 * u, ey + er + 1.5 * u],
        fill=STEEL_DARK,
    )
    if blink:
        d.line([(ex - er, ey), (ex + er, ey)], fill=INK, width=max(1, int(1.2 * u)))
    else:
        d.ellipse([ex - er, ey - er, ex + er, ey + er], fill=INK)
        glow = _lerp(LENS, LENS_GLOW, 0.25 + 0.75 * level)
        gr = er * (0.45 + 0.35 * level)
        d.ellipse([ex - gr, ey - gr, ex + gr, ey + gr], fill=glow)
        d.ellipse(
            [
                ex - gr * 0.35 + gr * 0.3,
                ey - gr * 0.35 - gr * 0.3,
                ex + gr * 0.35 + gr * 0.3,
                ey + gr * 0.35 - gr * 0.3,
            ],
            fill=(255, 255, 255),
        )

    # the oversized repair arm with a multimeter; lifts to "measure"
    lift = 1.0 if mood == "measure" else 0.0
    ang = math.radians(-20 - 55 * lift + 3 * math.sin(t * 1.7))
    sx, sy = bx0 + body_w * 0.95, by0 + body_h * 0.35 + bob * 0
    seg1 = 15 * u
    ex1, ey1 = sx + math.cos(ang) * seg1, sy + math.sin(ang) * seg1
    d.line([(sx, sy), (ex1, ey1)], fill=STEEL_DARK, width=int(6 * u))
    d.ellipse([sx - 4 * u, sy - 4 * u, sx + 4 * u, sy + 4 * u], fill=STEEL_DARK)
    ang2 = ang + math.radians(35 - 20 * lift)
    seg2 = 12 * u
    ex2, ey2 = ex1 + math.cos(ang2) * seg2, ey1 + math.sin(ang2) * seg2
    d.line([(ex1, ey1), (ex2, ey2)], fill=STEEL, width=int(5 * u))
    d.ellipse([ex1 - 3.5 * u, ey1 - 3.5 * u, ex1 + 3.5 * u, ey1 + 3.5 * u], fill=STEEL_DARK)
    # the multimeter: a small box with a readout and two probes
    mw, mh = 12 * u, 8 * u
    mx, my = ex2 - mw * 0.2, ey2 - mh / 2
    d.rounded_rectangle(
        [mx, my, mx + mw, my + mh],
        radius=int(1.5 * u),
        fill=(0xF2, 0xC9, 0x4A),
        outline=INK,
        width=1,
    )
    d.rectangle(
        [mx + 1.5 * u, my + 1.5 * u, mx + mw - 1.5 * u, my + mh * 0.55], fill=(0xDD, 0xE8, 0xD0)
    )
    reading = reading if mood == "measure" else "--.-"
    _text(d, (mx + 2 * u, my + 1.4 * u), reading, int(3.2 * u), INK, bold=True)
    d.line(
        [(mx + mw * 0.3, my + mh), (mx + mw * 0.2, my + mh + 4 * u)],
        fill=(0xC8, 0x3C, 0x3C),
        width=max(1, int(0.8 * u)),
    )
    d.line(
        [(mx + mw * 0.7, my + mh), (mx + mw * 0.8, my + mh + 4 * u)],
        fill=INK,
        width=max(1, int(0.8 * u)),
    )


def draw_falak(
    im: Image.Image,
    box: tuple[int, int, int, int],
    t: float,
    level: float = 0.0,
    mood: str = "idle",
) -> None:
    """Falak-1 in the box, panels tilting gently; the beacon blinks with the voice."""
    d = ImageDraw.Draw(im)
    x, y, w, h = box
    u = h / 100.0
    cx, cy = x + w / 2, y + h * 0.55
    drift = math.sin(t * 0.9) * 1.5 * u
    cy += drift
    tilt = math.radians(8 * math.sin(t * 0.6) + (10 if mood == "measure" else 0))

    # solar panels: two long wings, hinged at the body
    pw, ph = 38 * u, 14 * u
    for side in (-1, 1):
        x0 = cx + side * (12 * u)
        pts = []
        for px, py in ((0, -ph / 2), (pw, -ph / 2), (pw, ph / 2), (0, ph / 2)):
            px *= side
            rx = cx + side * 12 * u + (px * math.cos(tilt) - py * math.sin(tilt) * 0.3)
            ry = cy + (py * math.cos(tilt) + px * math.sin(tilt) * 0.15)
            pts.append((rx, ry))
        d.polygon(pts, fill=PANEL, outline=PANEL_LINE)
        # cell grid
        for k in range(1, 6):
            fx = k / 6
            ax = pts[0][0] + (pts[1][0] - pts[0][0]) * fx
            ay = pts[0][1] + (pts[1][1] - pts[0][1]) * fx
            bx = pts[3][0] + (pts[2][0] - pts[3][0]) * fx
            by = pts[3][1] + (pts[2][1] - pts[3][1]) * fx
            d.line([(ax, ay), (bx, by)], fill=PANEL_LINE, width=1)
        d.line(
            [(x0, cy - 1.5 * u), (x0 + side * 4 * u, cy - 1.5 * u)],
            fill=STEEL_DARK,
            width=int(2 * u),
        )

    # boxy body in worn gold foil with patches
    bw, bh = 24 * u, 26 * u
    d.rounded_rectangle(
        [cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2],
        radius=int(2 * u),
        fill=GOLD,
        outline=GOLD_DARK,
        width=max(1, int(0.8 * u)),
    )
    for fx, fy, fw, fh in ((0.1, 0.15, 0.3, 0.2), (0.55, 0.6, 0.35, 0.25), (0.2, 0.7, 0.2, 0.15)):
        d.rectangle(
            [
                cx - bw / 2 + bw * fx,
                cy - bh / 2 + bh * fy,
                cx - bw / 2 + bw * (fx + fw),
                cy - bh / 2 + bh * (fy + fh),
            ],
            fill=GOLD_DARK,
        )
    for k in range(4):  # foil crinkle lines
        yy = cy - bh / 2 + bh * (0.2 + 0.2 * k)
        d.line(
            [(cx - bw / 2 + 2 * u, yy), (cx + bw / 2 - 2 * u, yy + 1.5 * u)],
            fill=(0xE0, 0xBE, 0x5A),
            width=1,
        )

    # dish antenna on top, facing up-right
    ax, ay = cx + bw * 0.15, cy - bh / 2 - 9 * u
    d.line([(cx + bw * 0.1, cy - bh / 2), (ax, ay)], fill=STEEL_DARK, width=int(1.8 * u))
    d.pieslice(
        [ax - 9 * u, ay - 9 * u, ax + 9 * u, ay + 9 * u],
        200,
        340,
        fill=STEEL_LIGHT,
        outline=STEEL_DARK,
        width=1,
    )
    d.line([(ax, ay), (ax, ay - 6 * u)], fill=STEEL_DARK, width=max(1, int(0.9 * u)))
    d.ellipse([ax - 1.5 * u, ay - 7.5 * u, ax + 1.5 * u, ay - 4.5 * u], fill=STEEL_DARK)

    # the beacon: blinks slowly when idle, follows the voice when speaking
    on = (0.35 + 0.65 * level) if level > 0.05 else (1.0 if (t % 2.0) < 0.25 else 0.15)
    bxx, byy, br = cx - bw * 0.3, cy - bh / 2 - 2.5 * u, 2.6 * u
    d.line([(bxx, cy - bh / 2), (bxx, byy)], fill=STEEL_DARK, width=max(1, int(0.9 * u)))
    d.ellipse(
        [bxx - br, byy - br, bxx + br, byy + br],
        fill=_lerp((0x6A, 0x2A, 0x2A), (0xFF, 0x6B, 0x4A), on),
    )


def _text(
    d: ImageDraw.ImageDraw, xy: tuple[float, float], s: str, size: int, fill, bold: bool = False
) -> None:  # type: ignore[no-untyped-def]
    from cap.render import _font

    d.text(xy, s, font=_font(max(8, size), bold=bold), fill=fill)
