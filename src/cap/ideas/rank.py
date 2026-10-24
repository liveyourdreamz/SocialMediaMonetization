"""The code half of idea scoring (PRD 4.8, D48, D24): which candidates are held back,
how the rest are ranked, the evergreen and timely mix, the 90-day memory and the
revenue band. The model judged; nothing here asks a model anything.

Held back, never shown (each with its reason on the record):
* a Red topic (P5 ethics pre-check);
* evidence that is thin or promotional (P5 evidence check): boosterism never reaches
  the Digest;
* no primary source (P4 source_gap), or suspected injection on the page it came from;
* the same topic shown or posted in the last 90 days, unless its demand has at least
  doubled since.

Score, 0 to 1: demand (the pageview ratio on a log scale, capped) 35%, constructive
fit 25%, persona fit 20%, anchor fit 20%. The weights are settings to tune from the
first month's picks, not facts.

Revenue per post is a band, labelled an assumption until the pilot has view data: an
assumed first-month views range times the niche's RPM (research R1: education and
science about $10 per thousand views in English; Arabic about a fifth of that).
"""

from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import dataclass

from cap.ideas.demand import DemandSignal
from cap.schema.ideas import Idea, IdeaStatus

WEIGHTS = {"demand": 0.35, "constructive": 0.25, "persona": 0.20, "anchor": 0.20}
RATIO_CAP = 10.0  # a 10x rise or more scores full demand
EVERGREEN_SHARE = 0.7
VIEWS_ASSUMED = (1_000, 10_000)  # first-month views per post, an assumption (R1, D48)
RPM_USD = {"A": 10.0, "B": 10.0, "C": 2.0}  # R1: education and science; Arabic ~ 1/5
SAME_TOPIC = 0.6  # word overlap at which two topics are the same subject


@dataclass(frozen=True)
class Candidate:
    """One P4 item joined with its P5 judgement (the agents' outputs, validated)."""

    topic: str
    angle: str | None
    demand_ref: str | None
    evidence_urls: tuple[str, ...]
    timing: str
    source_gap: bool
    injection_suspected: bool
    ethics_precheck: str
    evidence_check: str | None
    constructive_fit: float | None
    persona_fit: float | None
    anchor_fit: float | None
    risk_lane: str
    explanation: str


_WORD = re.compile(r"\w+", re.UNICODE)
_STOP = {"the", "a", "an", "of", "how", "why", "what", "is", "are", "and", "in", "to", "it"}


def _fold(w: str) -> str:
    """English plural to singular ("pumps", "moves" -> "pump", "move"); "glass" stays."""
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def _words(text: str) -> set[str]:
    return {_fold(w) for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 2}


def same_topic(a: str, b: str) -> bool:
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return False
    return len(wa & wb) / len(wa | wb) >= SAME_TOPIC


def _clip(x: float | None) -> float:
    return 0.0 if x is None else min(max(float(x), 0.0), 1.0)


def demand_score(ratio: float) -> float:
    if ratio <= 1.0:
        return 0.0
    return round(min(math.log(ratio) / math.log(RATIO_CAP), 1.0), 4)


def score(c: Candidate, ratio: float) -> float:
    s = (
        WEIGHTS["demand"] * demand_score(ratio)
        + WEIGHTS["constructive"] * _clip(c.constructive_fit)
        + WEIGHTS["persona"] * _clip(c.persona_fit)
        + WEIGHTS["anchor"] * _clip(c.anchor_fit)
    )
    return round(s, 4)


def revenue_band(channel: str) -> tuple[float, float]:
    rpm = RPM_USD.get(channel, RPM_USD["C"])
    return (round(VIEWS_ASSUMED[0] * rpm / 1000, 2), round(VIEWS_ASSUMED[1] * rpm / 1000, 2))


def held_reason(c: Candidate) -> str:
    if c.ethics_precheck == "red":
        return "Red lever"
    if c.injection_suspected:
        return "the page tried to instruct the reader"
    if c.source_gap:
        return "no primary source"
    if c.evidence_check in ("thin", "promotional"):
        return f"evidence {c.evidence_check}"
    return ""


def remembered(topic: str, ratio: float, memory: list[Idea]) -> bool:
    """Shown or posted in the memory window, and demand has not at least doubled."""
    for past in memory:
        if same_topic(topic, past.topic_edit or past.topic):
            return not (past.ratio_to_baseline and ratio >= 2 * past.ratio_to_baseline)
    return False


def _mix(ranked: list[Idea], count: int, evergreen_share: float) -> list[Idea]:
    """The top `count`, with about `evergreen_share` evergreen; a short side is filled
    from the other, so the Digest is never shorter than it could be."""
    want_ever = round(count * evergreen_share)
    ever = [i for i in ranked if i.timing == "evergreen"][:want_ever]
    timely = [i for i in ranked if i.timing == "timely"][: count - len(ever)]
    chosen = {i.idea_id for i in ever + timely}
    rest = [i for i in ranked if i.idea_id not in chosen]
    picked = ever + timely + rest[: max(0, count - len(ever) - len(timely))]
    return sorted(picked, key=lambda i: i.score, reverse=True)


def build_week(
    channel: str,
    monday: dt.date,
    candidates: list[Candidate],
    demand: dict[str, DemandSignal],
    memory: list[Idea],
    count: int = 15,
    evergreen_share: float = EVERGREEN_SHARE,
) -> list[Idea]:
    """Every candidate as an Idea: the shown ones ranked 1..count, the rest held with
    their reason (held ones are kept for the memory and the audit, never shown)."""
    low, high = revenue_band(channel)
    out: list[Idea] = []
    for c in candidates:
        sig = demand.get(c.demand_ref or "")
        ratio = sig.ratio_to_baseline if sig else 1.0
        why = held_reason(c) or (
            "shown in the last 90 days" if remembered(c.topic, ratio, memory) else ""
        )
        out.append(
            Idea(
                channel=channel, week=monday, topic=c.topic, angle=c.angle,
                demand_title=sig.title if sig else None,
                views_7d=sig.views_7d if sig else 0, ratio_to_baseline=ratio,
                evidence_urls=list(c.evidence_urls), timing=c.timing,  # type: ignore[arg-type]
                source_gap=c.source_gap, injection_suspected=c.injection_suspected,
                ethics_precheck=c.ethics_precheck,  # type: ignore[arg-type]
                evidence_check=c.evidence_check,  # type: ignore[arg-type]
                constructive_fit=c.constructive_fit, persona_fit=c.persona_fit,
                anchor_fit=c.anchor_fit, risk_lane=c.risk_lane,  # type: ignore[arg-type]
                explanation=c.explanation, score=0.0 if why else score(c, ratio),
                revenue_low_usd=low, revenue_high_usd=high,
                status=IdeaStatus.HELD if why else IdeaStatus.SHOWN, held_reason=why,
            )
        )  # fmt: skip
    eligible = sorted(
        (i for i in out if i.status == IdeaStatus.SHOWN), key=lambda i: i.score, reverse=True
    )
    shown = _mix(eligible, count, evergreen_share)
    rank = {i.idea_id: n for n, i in enumerate(shown, start=1)}
    final = []
    for i in out:
        if i.idea_id in rank:
            final.append(i.model_copy(update={"rank": rank[i.idea_id]}))
        elif i.status == IdeaStatus.SHOWN:  # eligible but beyond the count
            final.append(
                i.model_copy(update={"status": IdeaStatus.HELD, "held_reason": "below the cut"})
            )
        else:
            final.append(i)
    return sorted(final, key=lambda i: (i.rank == 0, i.rank))
