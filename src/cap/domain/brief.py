"""The brief builder's code half (PRD 4.9, agent 3; Pack P6 "mostly code").

A brief is the contract the writer works to: the channel's one format, the anchor
elements, the claims the episode may make, its length, its risk lane and any sponsor.
Every field here is set by rule, so the same topic always gets the same brief:

* format: the character's signature format (D42: one per character);
* lane: high when the topic touches health, money, law, politics or children, so the
  writer is told to keep out of advice and outcomes and the approver gets longer on
  the card (R7 risk 5: risk-tiered lanes);
* claims scope: the mechanism and cited figures; on the high lane, no outcomes or
  advice in that area;
* sponsor: refused unless the UAE permit is granted and the NMA category is recorded
  as allowed; a sponsor's product never gets a verdict (R-10).

P6's model call (format, anchors and claims chosen from the idea's evidence) waits for
the idea pipeline: under D41 a brief is a topic with no evidence yet, and P6 as written
blocks a brief with no evidence.
"""

from __future__ import annotations

import re

from cap.schema.post_record import Brief, Lane, Sponsor

FORMATS: dict[str, str] = {"A": "take_it_apart", "B": "from_orbit", "C": "from_orbit"}

# The sensitive areas (P5's flag list) and words that put a topic in one. Matching is
# on whole words, case-insensitive, with an English plural ("-s", "-es") and the Arabic
# article ("ال") allowed; a miss leaves the lane low, and the ethics judges still read
# every script.
SENSITIVE: dict[str, tuple[str, ...]] = {
    "health": (
        "health", "medical", "medicine", "disease", "drug",
        "vaccine", "diet", "nutrition", "supplement",
        "cancer", "sleep", "mental", "symptom", "doctor",
        "hospital", "virus", "pregnancy", "weight loss", "calories",
        "صحة", "دواء", "مرض", "لقاح", "نوم",
    ),
    "money": (
        "money", "invest", "investing", "investment", "stock", "shares",
        "crypto", "bitcoin", "loan", "mortgage", "tax", "bank",
        "insurance", "salary", "pension", "retirement", "trading", "inflation",
        "مال", "استثمار", "بنك", "ضريبة",
    ),
    "law": (
        "law", "legal", "court", "lawsuit", "visa", "contract",
        "police", "crime", "قانون", "محكمة",
    ),
    "politics": (
        "election", "government", "politics", "political", "war",
        "vote", "voting", "president", "minister", "sanctions", "protest", "انتخابات",
        "حكومة", "حرب",
    ),
    "children": (
        "child", "children", "kid", "baby", "babies", "toddler", "teen",
        "teenager", "school", "طفل", "أطفال",
    ),
}  # fmt: skip
_AREA_RE = {
    area: re.compile(
        r"(?<!\w)(?:ال)?(" + "|".join(re.escape(w) for w in words) + r")(?:e?s)?(?!\w)", re.I
    )
    for area, words in SENSITIVE.items()
}

BASE_SCOPE = [
    "the mechanism as well-established sources describe it",
    "figures commonly cited, with their year",
    "first-hand results only from a test log, when one is given",
]


class BriefRefusedError(ValueError):
    """No brief can be built (a sponsor without a valid permit or category)."""


def sensitive_areas(topic: str) -> list[str]:
    """The sensitive areas a topic touches, in a fixed order."""
    return [area for area, rx in _AREA_RE.items() if rx.search(topic)]


def claims_scope(areas: list[str]) -> list[str]:
    scope = list(BASE_SCOPE)
    if areas:
        scope.append(
            f"no advice, outcomes or recommendations about {', '.join(areas)}: "
            "explain how it works, attribute any figure to a named authority"
        )
    return scope


def sponsor_refusal(sponsor: Sponsor | None) -> str | None:
    """Why a sponsored brief cannot be built, or None (no sponsor, or a valid one)."""
    if sponsor is None:
        return None
    if sponsor.permit_status != "granted":
        return f"sponsor {sponsor.name}: the UAE advertiser permit is {sponsor.permit_status}"
    if sponsor.nma_category_status != "allowed":
        return (
            f"sponsor {sponsor.name}: the product category's NMA status is "
            f"{sponsor.nma_category_status}, not recorded as allowed"
        )
    return None


def build_brief(
    channel: str,
    topic: str,
    *,
    anchor_elements: list[str],
    length_s: int,
    sponsor: Sponsor | None = None,
) -> Brief:
    """The brief for one topic. Raises BriefRefusedError for an invalid sponsor."""
    topic = topic.strip()
    if not topic:
        raise BriefRefusedError("a brief needs a topic")
    if channel not in FORMATS:
        raise BriefRefusedError(f"no format registered for channel {channel}")
    why = sponsor_refusal(sponsor)
    if why:
        raise BriefRefusedError(why)
    if sponsor is not None:
        sponsor.verdict_allowed = False  # R-10: never a verdict on the sponsor's product
    areas = sensitive_areas(topic)
    return Brief(
        topic=topic,
        format_id=FORMATS[channel],
        anchor_elements=list(anchor_elements[:2]),
        claims_allowed=claims_scope(areas),
        length_s=length_s,
        lane=Lane.HIGH if areas else Lane.LOW,
        sponsor_id=sponsor.sponsor_id if sponsor else None,
    )
