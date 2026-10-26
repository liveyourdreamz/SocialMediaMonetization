"""Planning: each spoken sentence becomes a scene, chosen from its words (never invented beyond them)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from cap.render.scenes.model import STOP, Scene


def _sentences(p: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?؟])\s+", p) if s.strip()]


def action_verb(low: str) -> str:
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


NUM = r"(\d+(?:[.,]\d+)?)"


def number_in(text: str) -> float:
    """A number as NUM captures it: "1,000" is a thousand (a comma is a separator)."""
    return float(text.replace(",", ""))


def units(paragraphs: list[str]) -> list[str]:
    """One scene per sentence: a paragraph usually covers several beats (the setup,
    the failure, the measurement, the verdict) and each deserves its own picture."""
    out: list[str] = []
    for p in paragraphs:
        out.extend(_sentences(p))
    return out


@dataclass
class _Sentence:
    """One sentence being planned, with what the rules read from it."""

    sc: Scene
    p: str
    low: str
    nums: list[float]
    timed: list[tuple[str, str]]
    tasks: list[str]
    memo: dict[str, float | None]  # "claimed": a claim remembered for the measurement


_UNIT = r"\s*(seconds?|secs?|minutes?|ms)"


def _rule_verdict(c: _Sentence) -> bool:
    if not re.search(r"\bverdict\b", c.low):
        return False
    c.sc.kind = "verdict"
    c.sc.mood = (
        "skip"
        if re.search(r"\bskip\b", c.low)
        else "watch"
        if re.search(r"\b(watch|caveat)\b", c.low)
        else "keep"
    )
    return True


def _rule_task(c: _Sentence) -> bool:
    task = re.search(r"\btask\s+(one|two|three|four|1|2|3|4)\b", c.low)
    if not task:
        return False
    c.sc.kind = "task"
    c.sc.index = {"one": 1, "two": 2, "three": 3, "four": 4}.get(task.group(1)) or int(
        task.group(1)
    )
    c.sc.items = c.tasks
    return True


def _rule_fail(c: _Sentence) -> bool:
    if not re.search(r"\b(fail|failed|crash|crashed|broke|error|errors)\b", c.low):
        return False
    c.sc.kind = "app_fail"
    c.sc.action = action_verb(c.low)
    c.sc.times = 2 if "twice" in c.low else 3 if "three times" in c.low else 1
    m = re.search(r"\bday\s+" + NUM, c.low)
    c.sc.day = int(number_in(m.group(1))) if m else None
    return True


def _rule_ratio(c: _Sentence) -> bool:
    ratio = re.search(NUM + r"\s+of\s+(?:the\s+)?" + NUM, c.low)
    if not ratio:
        return False
    c.sc.kind = "ratio"
    c.sc.number, c.sc.total = number_in(ratio.group(1)), int(number_in(ratio.group(2)))
    m = re.search(r"\b(invented|made up|wrong|missed)\s+" + NUM, c.low)
    c.sc.extra = number_in(m.group(2)) if m else None
    return True


def _rule_timer(c: _Sentence) -> bool:
    if not (c.timed and re.search(r"\b(measured|took|average|averaged)\b", c.low)):
        return False
    c.sc.kind, c.sc.mood = "timer", "measure"
    c.sc.action = action_verb(c.low)
    # the reading follows took/measured; the claim follows under/within/says
    meas = re.search(r"\b(?:measured|took|averaged?)\b[^.;]*?" + NUM + _UNIT, c.low)
    clm = re.search(r"\b(?:under|within|says|claims|promises)\b[^.;]*?" + NUM + _UNIT, c.low)
    if meas:
        c.sc.number, c.sc.unit = float(meas.group(1).replace(",", "")), meas.group(2)
    else:
        c.sc.number, c.sc.unit = float(c.timed[-1][0].replace(",", "")), c.timed[-1][1]
    if clm:
        c.memo["claimed"] = float(clm.group(1).replace(",", ""))
    c.sc.claimed = c.memo["claimed"]
    return True


def _rule_flow(c: _Sentence) -> bool:
    if not re.search(r"\b(locally|local|cloud|syncs|stores)\b", c.low):
        return False
    c.sc.kind = "flow"
    if c.timed:
        claimed = float(c.timed[-1][0].replace(",", ""))
        c.memo["claimed"] = claimed
        c.sc.label = f"claimed: within {claimed:g} {c.timed[-1][1]}"
    return True


def _rule_timed_claim(c: _Sentence) -> bool:
    if not (c.timed and re.search(r"\b(within|under|says|claims|promises)\b", c.low)):
        return False
    c.memo["claimed"] = float(c.timed[-1][0].replace(",", ""))
    c.sc.kind, c.sc.label = "claim", f"the claim: {c.timed[-1][0]} {c.timed[-1][1]}"
    return True


def _rule_said_claim(c: _Sentence) -> bool:
    if not (re.search(r"\b(says|claims|promises)\b", c.low) and c.sc.tool.lower() in c.low):
        return False
    c.sc.kind = "claim"
    m = re.search(r"\b(?:says|claims|promises)\b\s*(?:it\s+)?(.{12,90}?)(?:[.;,]|$)", c.p)
    c.sc.label = m.group(1).strip() if m else "the claim"
    return True


def _rule_calendar(c: _Sentence) -> bool:
    m = re.search(r"\b(" + NUM + r"|seven|five|ten|three)[- ]days?\b", c.low)
    if not m:
        return False
    c.sc.kind = "calendar"
    word = m.group(1)
    c.sc.days = {"seven": 7, "five": 5, "ten": 10, "three": 3}.get(word) or int(number_in(word))
    return True


# Patch's scenes: the first rule that matches the sentence decides; order matters
_PATCH_RULES = (
    _rule_verdict,
    _rule_task,
    _rule_fail,
    _rule_ratio,
    _rule_timer,
    _rule_flow,
    _rule_timed_claim,
    _rule_said_claim,
    _rule_calendar,
)


def _patch_scene(c: _Sentence, on_screen: list[str]) -> None:
    for rule in _PATCH_RULES:
        if rule(c):
            return
    if c.nums and not re.search(r"\btask\b", c.low):
        # D42: Patch explains anything; a number gets the measured-figure panel
        c.sc.kind, c.sc.number, c.sc.mood = "figure", c.nums[0], "measure"
        m = re.search(NUM + r"\s*([%\w]+)", c.p)
        c.sc.unit = m.group(2) if m else ""
    elif on_screen and "take it apart" in c.low:
        c.sc.kind, c.sc.items = "steps", on_screen[: min(4, len(on_screen))]


def _falak_scene(c: _Sentence, i: int, on_screen: list[str]) -> None:
    if i == 1 or "from orbit" in c.low or "من المدار" in c.p:
        c.sc.kind = "orbit"
    elif c.nums:
        c.sc.kind, c.sc.number = "figure", c.nums[0]
        m = re.search(NUM + r"\s*([%\w]+)", c.p)
        c.sc.unit = m.group(2) if m else ""
    elif on_screen and i < len(on_screen) + 2:
        c.sc.kind, c.sc.items = "steps", on_screen[: min(4, len(on_screen))]


def _follow_on(prev: Scene, p: str, tool: str) -> Scene:
    """A follow-on remark keeps the picture it comments on, in its finished state."""
    return Scene(
        kind=prev.kind, text=p, sentences=[p], tool=tool, mood=prev.mood,
        number=prev.number, unit=prev.unit, claimed=prev.claimed, day=prev.day,
        days=prev.days, times=prev.times, items=prev.items, label=prev.label,
        index=prev.index, total=prev.total, extra=prev.extra, hold=True,
        action=prev.action,
    )  # fmt: skip


def _checklist(sentences: list[str]) -> list[str]:
    """The checklist, from every "Task n: ..." sentence."""
    tasks = []
    for p in sentences:
        m = re.match(r"\s*task\s+(?:one|two|three|four|\d)\s*[:,-]\s*(.+?)(?:[.;]|$)", p, re.I)
        if m:
            tasks.append(m.group(1).strip())
    return tasks


def plan(
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
    memo: dict[str, float | None] = {"claimed": None}
    tasks = _checklist(sentences)
    for i, p in enumerate(sentences):
        low = p.lower()
        c = _Sentence(
            sc=Scene(kind="talk", text=p, sentences=[p], tool=tool),
            p=p,
            low=low,
            nums=[float(n.replace(",", "")) for n in re.findall(NUM, p)],
            timed=re.findall(NUM + r"\s*(seconds?|secs?|minutes?|ms)\b", low),
            tasks=tasks,
            memo=memo,
        )
        if media_left and i > 0:
            c.sc.kind, c.sc.media = "media", media_left.pop(0)
        elif character == "Patch":
            _patch_scene(c, on_screen)
        else:
            _falak_scene(c, i, on_screen)
        sc = c.sc
        if sc.kind == "talk" and scenes and scenes[-1].kind not in ("talk", "media") and i > 1:
            sc = _follow_on(scenes[-1], p, tool)
        scenes.append(sc)
    return scenes
