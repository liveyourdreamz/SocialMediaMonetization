"""Scenes: the picture of an episode, planned from the approved record and drawn frame
by frame.

Each spoken paragraph becomes one scene. The character stands on screen and speaks
(the lens or beacon follows the voice); the screen panel beside it shows what the
paragraph is about: the app's sync bar failing, a stopwatch running to the measured
number, the local-then-cloud flow, the seven-day strip, the verdict stamp, a figure
from a source. What is shown is chosen from the words of the script and the claims,
never invented beyond them, and every reconstructed app view is labelled as such.
Uploaded screenshots in the brief take the panel instead when present.

Layout, landscape: character left, screen panel right, the spoken sentence below.
Portrait (the Short): panel top, character bottom-left, sentence beside it.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw

from cap.render.characters import draw_falak, draw_patch

FPS = 15  # typographic scenes and a bobbing robot: 15 fps is smooth and 40% cheaper
GROUND = (0xF6, 0xF4, 0xEF)
SURFACE = (0xFF, 0xFF, 0xFF)
INK = (0x1B, 0x1A, 0x17)
MUTED = (0x5C, 0x59, 0x55)
LINE = (0xDD, 0xD9, 0xD0)
ACCENT = (0x1F, 0x5F, 0x73)
BEZEL = (0x2A, 0x29, 0x26)
OK = (0x2E, 0x9E, 0x5B)
WARN = (0xE8, 0xB9, 0x2E)
BAD = (0xC8, 0x3C, 0x3C)
SKY = (0x0B, 0x1E, 0x33)
EARTH = (0x2F, 0x6F, 0x9F)
LAND = (0x5F, 0x8F, 0x4A)

STOP = {
    "the",
    "a",
    "an",
    "we",
    "i",
    "on",
    "day",
    "days",
    "for",
    "today",
    "test",
    "tested",
    "verdict",
    "keep",
    "skip",
    "watch",
    "our",
    "my",
    "this",
    "that",
    "sync",
    "how",
    "why",
    "what",
    "when",
    "with",
    "and",
    "or",
    "of",
    "in",
    "to",
    "it",
    "is",
    "its",
    "at",
    "by",
    "from",
    "patch",
    "falak",
    "ai",
    "tool",
    "app",
    "seven",
    "week",
    "short",
    "one",
}


@dataclass
class Scene:
    kind: (
        str  # intro, app_fail, timer, flow, calendar, verdict, figure, orbit, media, talk,
        # sources, opening_slot (the review render's silent stand-in for the opening line)
    )
    text: str  # the spoken paragraph
    sentences: list[str]
    tool: str = ""
    mood: str = "idle"
    number: float | None = None  # timer target / figure
    unit: str = ""
    claimed: float | None = None  # a second number in the same breath (claimed vs measured)
    day: int | None = None
    days: int = 7
    times: int = 1  # "twice" -> 2
    media: Path | None = None
    items: list[str] = field(default_factory=list)
    label: str = ""
    index: int = 0  # task number for the checklist
    total: int = 0  # the "of N" in a ratio
    extra: float | None = None  # "invented 2": the figure that counts against
    hold: bool = False  # a follow-on sentence keeps the previous panel, finished
    action: str = "Sync"  # the verb the status row names: Sync, Export, Upload, ...


# ------------------------------------------------------------------ planning


def _sentences(p: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?؟])\s+", p) if s.strip()]


def _action(low: str) -> str:
    """The thing the app was doing, for the status row: Sync, Export, Transcript..."""
    for verb, word in (
        ("sync", "Sync"),
        ("export", "Export"),
        ("upload", "Upload"),
        ("transcri", "Transcript"),
        ("import", "Import"),
        ("save", "Save"),
        ("login", "Sign-in"),
        ("render", "Render"),
        ("search", "Search"),
    ):
        if verb in low:
            return word
    return "Task"


def tool_name(title: str, topic: str) -> str:
    """The product under test: the first capitalised word that is not a common one."""
    for text in (topic, title):
        for w in re.findall(r"[A-Z][A-Za-z0-9]+", text):
            if w.lower() not in STOP:
                return w
    return "the tool"


_NUM = r"(\d+(?:[.,]\d+)?)"


def _number(text: str) -> float:
    """A number as _NUM captures it: "1,000" is a thousand (a comma is a separator)."""
    return float(text.replace(",", ""))


def units(paragraphs: list[str]) -> list[str]:
    """One scene per sentence: a paragraph usually covers several beats (the setup,
    the failure, the measurement, the verdict) and each deserves its own picture."""
    out: list[str] = []
    for p in paragraphs:
        out.extend(_sentences(p))
    return out


def plan(  # noqa: C901 - M1 step 4 splits it
    sentences: list[str],
    *,
    character: str,
    title: str,
    topic: str,
    on_screen: list[str],
    media: list[Path],
    arabic: bool = False,
) -> list[Scene]:
    tool = tool_name(title, topic) if character == "Patch" else ""
    scenes: list[Scene] = []
    media_left = list(media)
    claimed: float | None = None  # "within 10 seconds" remembered for the measurement
    tasks: list[str] = []
    for p in sentences:  # the checklist, from every "Task n: ..." sentence
        m = re.match(r"\s*task\s+(?:one|two|three|four|\d)\s*[:,-]\s*(.+?)(?:[.;]|$)", p, re.I)
        if m:
            tasks.append(m.group(1).strip())
    for i, p in enumerate(sentences):
        low = p.lower()
        sc = Scene(kind="talk", text=p, sentences=[p], tool=tool)
        nums = [float(n.replace(",", "")) for n in re.findall(_NUM, p)]
        timed = re.findall(_NUM + r"\s*(seconds?|secs?|minutes?|ms)\b", low)
        if media_left and i > 0:
            sc.kind, sc.media = "media", media_left.pop(0)
        elif character == "Patch":
            task = re.search(r"\btask\s+(one|two|three|four|1|2|3|4)\b", low)
            ratio = re.search(_NUM + r"\s+of\s+(?:the\s+)?" + _NUM, low)
            if re.search(r"\bverdict\b", low):
                sc.kind = "verdict"
                sc.mood = (
                    "skip"
                    if re.search(r"\bskip\b", low)
                    else "watch"
                    if re.search(r"\b(watch|caveat)\b", low)
                    else "keep"
                )
            elif task:
                sc.kind = "task"
                sc.index = {"one": 1, "two": 2, "three": 3, "four": 4}.get(task.group(1)) or int(
                    task.group(1)
                )
                sc.items = tasks
            elif re.search(r"\b(fail|failed|crash|crashed|broke|error|errors)\b", low):
                sc.kind = "app_fail"
                sc.action = _action(low)
                sc.times = 2 if "twice" in low else 3 if "three times" in low else 1
                m = re.search(r"\bday\s+" + _NUM, low)
                sc.day = int(_number(m.group(1))) if m else None
            elif ratio:
                sc.kind = "ratio"
                sc.number, sc.total = _number(ratio.group(1)), int(_number(ratio.group(2)))
                m = re.search(r"\b(invented|made up|wrong|missed)\s+" + _NUM, low)
                sc.extra = _number(m.group(2)) if m else None
            elif timed and re.search(r"\b(measured|took|average|averaged)\b", low):
                sc.kind, sc.mood = "timer", "measure"
                sc.action = _action(low)
                # the reading follows took/measured; the claim follows under/within/says
                meas = re.search(
                    r"\b(?:measured|took|averaged?)\b[^.;]*?"
                    + _NUM
                    + r"\s*(seconds?|secs?|minutes?|ms)",
                    low,
                )
                clm = re.search(
                    r"\b(?:under|within|says|claims|promises)\b[^.;]*?"
                    + _NUM
                    + r"\s*(seconds?|secs?|minutes?|ms)",
                    low,
                )
                if meas:
                    sc.number, sc.unit = float(meas.group(1).replace(",", "")), meas.group(2)
                else:
                    sc.number, sc.unit = float(timed[-1][0].replace(",", "")), timed[-1][1]
                if clm:
                    claimed = float(clm.group(1).replace(",", ""))
                sc.claimed = claimed
            elif re.search(r"\b(locally|local|cloud|syncs|stores)\b", low):
                sc.kind = "flow"
                if timed:
                    claimed = float(timed[-1][0].replace(",", ""))
                    sc.label = f"claimed: within {claimed:g} {timed[-1][1]}"
            elif timed and re.search(r"\b(within|under|says|claims|promises)\b", low):
                claimed = float(timed[-1][0].replace(",", ""))
                sc.kind, sc.label = "claim", f"the claim: {timed[-1][0]} {timed[-1][1]}"
            elif re.search(r"\b(says|claims|promises)\b", low) and sc.tool.lower() in low:
                sc.kind = "claim"
                m = re.search(r"\b(?:says|claims|promises)\b\s*(?:it\s+)?(.{12,90}?)(?:[.;,]|$)", p)
                sc.label = m.group(1).strip() if m else "the claim"
            elif re.search(r"\b(" + _NUM + r"|seven|five|ten|three)[- ]days?\b", low):
                sc.kind = "calendar"
                m = re.search(r"\b(" + _NUM + r"|seven|five|ten|three)[- ]days?", low)
                word = m.group(1) if m else "7"
                sc.days = {"seven": 7, "five": 5, "ten": 10, "three": 3}.get(word) or int(
                    _number(word)
                )
            if sc.kind == "talk" and nums and not re.search(r"\btask\b", low):
                # D42: Patch explains anything; a number gets the measured-figure panel
                sc.kind, sc.number, sc.mood = "figure", nums[0], "measure"
                m = re.search(_NUM + r"\s*([%\w]+)", p)
                sc.unit = m.group(2) if m else ""
            elif sc.kind == "talk" and on_screen and "take it apart" in low:
                sc.kind, sc.items = "steps", on_screen[: min(4, len(on_screen))]
        else:  # Falak
            if i == 1 or "from orbit" in low or "من المدار" in p:
                sc.kind = "orbit"
            elif nums:
                sc.kind, sc.number = "figure", nums[0]
                m = re.search(_NUM + r"\s*([%\w]+)", p)
                sc.unit = m.group(2) if m else ""
            elif on_screen and i < len(on_screen) + 2:
                sc.kind, sc.items = "steps", on_screen[: min(4, len(on_screen))]
        if sc.kind == "talk" and scenes and scenes[-1].kind not in ("talk", "media") and i > 1:
            # a follow-on remark keeps the picture it comments on, in its finished state
            prev = scenes[-1]
            sc = Scene(
                kind=prev.kind, text=p, sentences=[p], tool=tool, mood=prev.mood,
                number=prev.number, unit=prev.unit, claimed=prev.claimed, day=prev.day,
                days=prev.days, times=prev.times, items=prev.items, label=prev.label,
                index=prev.index, total=prev.total, extra=prev.extra, hold=True,
                action=prev.action,
            )  # fmt: skip
        scenes.append(sc)
    return scenes


# ------------------------------------------------------------------ drawing helpers


def _font(size: int, bold: bool = False, arabic: bool = False):  # type: ignore[no-untyped-def]
    from cap.render import _font as f

    return f(size, bold, arabic)


def _wrap(d, text, font, width, arabic=False):  # type: ignore[no-untyped-def]
    from cap.render import _wrap as w

    return w(d, text, font, width, arabic)


def _shape(text: str, arabic: bool) -> str:
    from cap.render import _shape as s

    return s(text, arabic)


def _kw(arabic: bool) -> dict:
    from cap.render import _kw as k

    return k(arabic)


def _ease(f: float) -> float:
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


def _chrome(d: ImageDraw.ImageDraw, lay: Layout, character: str, channel: str) -> None:
    from cap.policy import AI_ROLE_DISCLOSURE

    w, h = lay.size
    m = lay.margin
    s = int(min(w, h) * 0.026)
    d.text((m, m * 0.7), f"{channel} · {character}".upper(), font=_font(s, bold=True), fill=ACCENT)
    label = AI_ROLE_DISCLOSURE.rstrip(".")
    f = _font(int(s * 0.9))
    tw = d.textlength(label, font=f)
    d.text((w - m - tw, m * 0.7), label, font=f, fill=MUTED)


def _panel_frame(d: ImageDraw.ImageDraw, lay: Layout) -> tuple[int, int, int, int]:
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


def _app_window(
    d: ImageDraw.ImageDraw, inner: tuple[int, int, int, int], tool: str
) -> tuple[int, int, int, int]:
    """A generic app window with a title bar; returns the content area."""
    x, y, w, h = inner
    bar = int(h * 0.11)
    d.rectangle([x, y, x + w, y + bar], fill=(0xEE, 0xEC, 0xE6))
    for i, c in enumerate(((0xE0, 0x5A, 0x4E), (0xE8, 0xB9, 0x2E), (0x2E, 0x9E, 0x5B))):
        cx = x + bar * 0.6 + i * bar * 0.55
        d.ellipse([cx - bar * 0.16, y + bar * 0.34, cx + bar * 0.16, y + bar * 0.66], fill=c)
    f = _font(int(bar * 0.42), bold=True)
    d.text((x + bar * 2.4, y + bar * 0.27), tool, font=f, fill=INK)
    tag = "reconstruction of the logged event"
    ft = _font(int(bar * 0.3))
    d.text(
        (x + w - d.textlength(tag, font=ft) - bar * 0.5, y + bar * 0.33), tag, font=ft, fill=MUTED
    )
    return (x, y + bar, w, h - bar)


def _notes(
    d: ImageDraw.ImageDraw, area: tuple[int, int, int, int], tool: str, rows: int = 4
) -> None:
    x, y, w, h = area
    pad = int(h * 0.06)
    fh = _font(int(h * 0.07), bold=True)
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


def _sync_bar(
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
    f = _font(int(h * 0.065), bold=True)
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


def _subtitle(d: ImageDraw.ImageDraw, lay: Layout, sentence: str, arabic: bool) -> None:
    x, y, w, h = lay.subtitle
    size = int(min(lay.size) * (0.036 if not lay.portrait else 0.034))
    f = _font(size, arabic=arabic)
    lines = _wrap(d, sentence, f, w, arabic)[: 4 if lay.portrait else 3]
    lh = int(size * 1.35)
    yy = y
    for ln in lines:
        txt = _shape(ln, arabic)
        xx = x + w - d.textlength(txt, font=f, **_kw(arabic)) if arabic else x
        d.text((xx, yy), txt, font=f, fill=INK, **_kw(arabic))
        yy += lh


# ------------------------------------------------------------------ the panel per scene


def _draw_panel(  # noqa: C901 - M1 step 4 splits it
    d: ImageDraw.ImageDraw,
    im: Image.Image,
    sc: Scene,
    inner: tuple[int, int, int, int],
    f: float,
    t: float,
    arabic: bool,
) -> None:
    x, y, w, h = inner
    pad = int(h * 0.07)
    if sc.kind == "media" and sc.media and sc.media.exists():
        try:
            pic = Image.open(sc.media).convert("RGB")
            pic.thumbnail((w, h))
            im.paste(pic, (x + (w - pic.width) // 2, y + (h - pic.height) // 2))
            return
        except OSError:
            pass
    if sc.kind == "app_fail":
        area = _app_window(d, inner, sc.tool)
        _notes(d, area, sc.tool)
        # the bar creeps, then the failure lands (once per "time"), then retries
        phase = (f * (sc.times + 0.5)) % 1.0 if sc.times > 1 else f
        state = "failed" if 0.55 < phase < 0.95 else "syncing"
        _sync_bar(d, area, phase * 1.6, state, t, sc.action)
        if sc.day:
            ft = _font(int(h * 0.07), bold=True)
            d.text(
                (x + w - pad - d.textlength(f"Day {sc.day}", font=ft), y + int(h * 0.16)),
                f"Day {sc.day}",
                font=ft,
                fill=BAD,
            )
        return
    if sc.kind == "timer":
        area = _app_window(d, inner, sc.tool)
        ax, ay, aw, ah = area
        target = sc.number or 0.0
        val = target * _ease(f * 1.15)
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
        big = _font(int(ah * 0.3), bold=True)
        txt = f"{val:.1f}" if target < 100 else f"{val:.0f}"
        d.text((ax + aw * 0.46, ay + ah * 0.2), txt, font=big, fill=INK)
        fu = _font(int(ah * 0.085))
        d.text((ax + aw * 0.47, ay + ah * 0.55), f"{sc.unit} measured", font=fu, fill=MUTED)
        if sc.claimed is not None:
            fc = _font(int(ah * 0.07), bold=True)
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
                fo = _font(int(ah * 0.075), bold=True)
                d.text((ax + aw * 0.47, ay + ah * 0.66), over, font=fo, fill=BAD)
        _sync_bar(d, area, f, "syncing" if f < 0.98 else "ok", t, sc.action)
        return
    if sc.kind == "flow":
        area = _app_window(d, inner, sc.tool)
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
        fl = _font(int(ah * 0.07), bold=True)
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
        ft = _font(int(ah * 0.065))
        d.text((dx, dy + dh + ah * 0.06), "1. saved locally first", font=ft, fill=INK)
        d.text((x0 + (x1 - x0) * 0.25, yy - ah * 0.12), "2. then synced", font=ft, fill=MUTED)
        if sc.label:
            fc = _font(int(ah * 0.07), bold=True)
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
    if sc.kind == "calendar":
        area = _app_window(d, inner, sc.tool)
        ax, ay, aw, ah = area
        n = max(1, min(sc.days, 14))
        gap = int(aw * 0.015)
        cw = (aw - pad * 2 - gap * (n - 1)) / n
        lit = int(n * min(1.0, f * 1.2))
        fd = _font(int(cw * 0.26), bold=True)
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
        ft = _font(int(ah * 0.08), bold=True)
        d.text((ax + pad, ay + ah * 0.1), f"{sc.days} days of logged tests", font=ft, fill=INK)
        return
    if sc.kind == "verdict":
        area = _app_window(d, inner, sc.tool)
        ax, ay, aw, ah = area
        word = {"keep": "KEEP", "skip": "SKIP", "watch": "WATCH"}[sc.mood]
        colour = {"keep": OK, "skip": BAD, "watch": WARN}[sc.mood]
        s = _ease(f * 2.2)
        fs = _font(int(ah * 0.3 * (0.6 + 0.4 * s)), bold=True)
        tw = d.textlength(word, font=fs)
        cx, cy = ax + aw / 2, ay + ah * 0.42
        d.rounded_rectangle(
            [cx - tw / 2 - pad, cy - fs.size * 0.65, cx + tw / 2 + pad, cy + fs.size * 0.65],
            radius=int(ah * 0.03),
            outline=colour,
            width=max(4, int(ah * 0.02)),
        )
        d.text((cx - tw / 2, cy - fs.size * 0.62), word, font=fs, fill=colour)
        ft = _font(int(ah * 0.07))
        sub = "from the test log"
        d.text((cx - d.textlength(sub, font=ft) / 2, cy + fs.size * 0.8), sub, font=ft, fill=MUTED)
        return
    if sc.kind == "orbit":
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
        zoom = _ease(f)
        bx = x + w * (0.25 + 0.4 * zoom)
        by = y + h * (0.62 + 0.1 * zoom)
        br = h * (0.015 + 0.04 * zoom)
        d.ellipse([bx - br, by - br, bx + br, by + br], outline=(0xFF, 0xD4, 0x6A), width=3)
        fl = _font(int(h * 0.07), bold=True)
        d.text((x + pad, y + pad), "from orbit", font=fl, fill=(0xE8, 0xEE, 0xF4))
        return
    if sc.kind == "figure":
        big = _font(int(h * 0.36), bold=True)
        val = (sc.number or 0) * _ease(f * 1.2)
        txt = f"{val:,.0f}" if (sc.number or 0) >= 100 else f"{val:.1f}"
        tw = d.textlength(txt, font=big)
        d.text((x + w / 2 - tw / 2, y + h * 0.2), txt, font=big, fill=INK)
        fu = _font(int(h * 0.09))
        d.text(
            (x + w / 2 - d.textlength(sc.unit, font=fu) / 2, y + h * 0.62),
            sc.unit,
            font=fu,
            fill=MUTED,
        )
        ft = _font(int(h * 0.055))
        d.text(
            (x + pad, y + h - pad - ft.size), "from the logbook, source cited", font=ft, fill=ACCENT
        )
        return
    if sc.kind == "steps":
        fs = _font(int(h * 0.075), bold=True, arabic=arabic)
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
            txt = _shape(f"{k + 1}. {item}", arabic)
            d.text(
                (x + pad * 2, yy + int(h * 0.045)),
                txt,
                font=fs,
                fill=INK if on else LINE,
                **_kw(arabic),
            )
            yy += int(h * 0.21)
        return
    if sc.kind == "task":
        area = _app_window(d, inner, sc.tool)
        ax, ay, aw, ah = area
        fh = _font(int(ah * 0.085), bold=True)
        d.text((ax + pad, ay + pad * 0.8), "The test: three tasks", font=fh, fill=INK)
        items = sc.items or ["task one", "task two", "task three"]
        fi = _font(int(ah * 0.075))
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
    if sc.kind == "ratio":
        area = _app_window(d, inner, sc.tool)
        ax, ay, aw, ah = area
        found, total = sc.number or 0.0, max(1, sc.total)
        prog = _ease(f * 1.3)
        fh = _font(int(ah * 0.085), bold=True)
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
        big = _font(int(ah * 0.16), bold=True)
        d.text((ax + pad, yy + bw + ah * 0.08), f"{found:g} of {total}", font=big, fill=INK)
        fs = _font(int(ah * 0.07))
        d.text(
            (ax + pad, yy + bw + ah * 0.26),
            "found, of the items written down by hand",
            font=fs,
            fill=MUTED,
        )
        if sc.extra:
            fb = _font(int(ah * 0.09), bold=True)
            msg = f"{sc.extra:g} invented"
            d.text(
                (ax + aw - pad - d.textlength(msg, font=fb), yy + bw + ah * 0.1),
                msg,
                font=fb,
                fill=BAD,
            )
        return
    if sc.kind == "claim":
        area = _app_window(d, inner, sc.tool)
        ax, ay, aw, ah = area
        _notes(d, area, sc.tool, rows=3)
        fc = _font(int(ah * 0.075), bold=True)
        lines = _wrap(d, f"\u201c{sc.label}\u201d", fc, aw - 2 * pad - int(ah * 0.1))
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
        ft = _font(int(ah * 0.055))
        d.text(
            (ax + pad, ay + ah - pad - bh - int(ah * 0.02) - ft.size * 1.4),
            "the vendor's claim, from the help page",
            font=ft,
            fill=MUTED,
        )
        return
    # talk: the key sentence large on the panel, the character does the work
    fs = _font(int(h * (0.1 if len(sc.sentences[0]) < 60 else 0.08)), bold=True, arabic=arabic)
    key = sc.sentences[0] if sc.sentences else sc.text
    lines = _wrap(d, key, fs, w - 2 * pad, arabic)[:5]
    yy = y + h / 2 - len(lines) * fs.size * 1.25 / 2
    for ln in lines:
        txt = _shape(ln, arabic)
        xx = x + w - pad - d.textlength(txt, font=fs, **_kw(arabic)) if arabic else x + pad
        d.text((xx, yy), txt, font=fs, fill=INK, **_kw(arabic))
        yy += fs.size * 1.25


def _sources_panel(
    d: ImageDraw.ImageDraw, inner: tuple[int, int, int, int], sources: list[str]
) -> None:
    x, y, w, h = inner
    pad = int(h * 0.07)
    d.text((x + pad, y + pad), "Sources", font=_font(int(h * 0.1), bold=True), fill=INK)
    f = _font(int(h * 0.055))
    yy = y + pad + int(h * 0.16)
    for s in sources[:6]:
        for ln in _wrap(d, s, f, w - 2 * pad)[:2]:
            d.text((x + pad, yy), ln, font=f, fill=MUTED)
            yy += int(f.size * 1.4)
        yy += int(f.size * 0.4)
    if not sources:
        d.text((x + pad, yy), "the presenter's own test log", font=f, fill=MUTED)


# ------------------------------------------------------------------ frames


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
    _chrome(d, lay, character, channel)
    f = 1.0 if sc.hold else i / max(1, n - 1)
    inner = _panel_frame(d, lay)
    if sc.kind == "sources":
        _sources_panel(d, inner, sources or [])
    else:
        _draw_panel(d, im, sc, inner, f, t_total, arabic)
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
        _subtitle(d, lay, sc.sentences[k], arabic)
    return im


def loudness(mp3: Path, n_frames: int) -> list[float]:
    """Per-frame voice level 0..1 from the audio, so the character speaks in time."""
    import array
    import subprocess

    from cap.render import _ffmpeg_exe

    r = subprocess.run(
        [
            _ffmpeg_exe(),
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
    _chrome(d, lay, character, channel)
    box = (int(w * 0.02), int(h * 0.16), int(w * 0.3), int(h * 0.76))
    (draw_falak if character == "Falak" else draw_patch)(
        im, box, 1.3, 0.6, "keep" if character == "Patch" else "idle"
    )
    fs = _font(int(h * 0.15), bold=True, arabic=arabic)
    lines = _wrap(d, text, fs, int(w * 0.5), arabic)[:3]
    block = len(lines) * fs.size * 1.12 + (h * 0.09 if tool else 0)
    yy = h * 0.52 - block / 2
    x_text = int(w * 0.46)
    for ln in lines:
        txt = _shape(ln, arabic)
        xx = w - int(w * 0.05) - d.textlength(txt, font=fs, **_kw(arabic)) if arabic else x_text
        d.text((xx, yy), txt, font=fs, fill=INK, **_kw(arabic))
        yy += fs.size * 1.12
    if tool:
        ft = _font(int(h * 0.07), bold=True)
        d.text((x_text, yy + h * 0.015), tool, font=ft, fill=ACCENT)
    return im
