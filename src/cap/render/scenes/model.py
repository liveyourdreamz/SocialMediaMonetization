"""The palette, the timing and the Scene: what every part of the picture shares."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

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
