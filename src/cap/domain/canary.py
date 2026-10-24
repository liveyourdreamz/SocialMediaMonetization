"""When to seed a canary and which fault it carries (Prompt Pack P14, D23, D34).

Rate, by the number of cards the reviewer has closed (canaries included):
* 1 in 3 for the first 15 cards, and for the 10 cards after any miss;
* 1 in 5 until card 100;
* 1 in 10 after that.
The settings hold the schedule as (one in N, until card M), M = 0 meaning "from then
on" (config.canary_schedule).

Spacing: the gap to the next canary is drawn at random between 1 and 2N - 1 cards
(mean N), so the reviewer cannot learn the rhythm; it is drawn once and kept in the
app state, so a restart never redraws it.

Fault: the type used least recently goes next; a type whose last canary no gate caught
(gate-blind) counts double, so the faults the machines miss come round more often. No
type is ever retired.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass

from cap.config import get_settings
from cap.schema.enums import FaultType

AFTER_MISS_CARDS, AFTER_MISS_RATE = 10, 3


@dataclass(frozen=True)
class ClosedCard:
    """One card the reviewer closed, oldest first: was it a canary, and the outcome."""

    canary: bool
    outcome: str = ""  # caught | missed | "" for a clean post


@dataclass(frozen=True)
class PastCanary:
    """A canary already written, oldest first."""

    fault: FaultType
    gate_blind: bool  # no gate caught it


def rate(cards: Sequence[ClosedCard]) -> int:
    """N in "one canary in N cards" for the next card."""
    closed = len(cards)
    misses = [k for k, c in enumerate(cards) if c.canary and c.outcome == "missed"]
    if misses and closed - (misses[-1] + 1) < AFTER_MISS_CARDS:
        return AFTER_MISS_RATE
    schedule = get_settings().canary_schedule
    for n, until in schedule:
        if until == 0 or closed < until:
            return n
    return schedule[-1][0]


def draw_gap(n: int, rng: random.Random) -> int:
    """Cards to wait before the next canary: uniform on 1..2N-1 (mean N)."""
    return rng.randint(1, max(1, 2 * n - 1))


def cards_since_last_canary(cards: Sequence[ClosedCard]) -> int:
    for k, c in enumerate(reversed(cards)):
        if c.canary:
            return k
    return len(cards)


def next_fault(past: Sequence[PastCanary]) -> FaultType:
    """The least recently used fault type; gate-blind types count double."""
    best, best_score = None, -1.0
    for fault in FaultType:  # enum order breaks ties: deterministic
        uses = [k for k, p in enumerate(past) if p.fault == fault]
        if not uses:
            return fault  # never used: first
        since = len(past) - uses[-1]
        score = since * (2 if past[uses[-1]].gate_blind else 1)
        if score > best_score:
            best, best_score = fault, score
    assert best is not None
    return best
