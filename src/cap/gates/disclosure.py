"""Gate 9: disclosure and labelling (PRD 4.13, Prompt Pack P9, D24).

* ai_label: always true for Patch and Falak (disclosed AI characters, D30); realistic
  synthetic media is labelled by rule R-06a regardless.
* paid_promo: true whenever the post carries a sponsor record, a sponsor id, or an
  affiliate marker anywhere in title, caption or script. When paid, every variant's
  title and caption must start with "Ad" (Pack P9 header; fault type missing_ad_prefix).
* made_for_kids: P9 returns factors only; code turns factors into a score with fixed
  weights and a band. The factor table with its weights goes to the model as a static
  block so the ids it returns are the ids the code weights; an id the code does not
  know makes the output unscored (fail closed), never silently ignored. "Unclear" on
  either of the two heaviest factors is doubtful whatever the score. Doubtful never
  auto-decides: the pipeline parks the post for Hemant (PRD 3, 4.13).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from cap.agents.outputs import P9Output
from cap.models import ModelClient
from cap.policy import AD_PREFIX, AFFILIATE_MARKERS
from cap.prompts import spec
from cap.schema.post_record import DisclosureGate, PostRecord

# COPPA / YouTube MFK factors with weights. Code constants; changed only with a signed
# decision. Positive only: adult subject matter is the absence of the child factor,
# not a counterweight that can cancel child-directed presentation (S7 in PRD 7).
MFK_FACTOR_WEIGHTS: dict[str, float] = {
    "child_subject_matter": 0.30,
    "intended_audience_children": 0.30,
    "child_characters_or_toys": 0.20,
    "child_oriented_language": 0.15,
    "child_oriented_music_or_animation": 0.10,
    "child_celebrities_or_influencers": 0.10,
}
HEAVIEST = ("child_subject_matter", "intended_audience_children")
MFK_LIKELY = 0.60
MFK_DOUBTFUL = 0.30

Band = Literal["clear", "doubtful", "likely", "unscored"]


def mfk_factor_block() -> str:
    lines = ['<mfk_factors version="1.0">']
    for fid, w in MFK_FACTOR_WEIGHTS.items():
        lines.append(f"{fid} (weight {w:.2f})")
    lines.append(
        "Return one entry per factor id above, outcome present, absent or unclear, with the "
        "span that shows it. Use these ids and no others."
    )
    lines.append("</mfk_factors>")
    return "\n".join(lines)


def mfk_score(factors: Sequence[tuple[str, str]]) -> float:
    """factors: (factor_id, outcome). Unclear counts half. Clipped to [0, 1]."""
    score = 0.0
    for fid, outcome in factors:
        w = MFK_FACTOR_WEIGHTS[fid]
        if outcome == "present":
            score += w
        elif outcome == "unclear":
            score += w / 2
    return max(0.0, min(1.0, score))


def mfk_band(score: float, factors: Sequence[tuple[str, str]] = ()) -> Band:
    if score >= MFK_LIKELY:
        return "likely"
    if score >= MFK_DOUBTFUL:
        return "doubtful"
    if any(fid in HEAVIEST and out == "unclear" for fid, out in factors):
        return "doubtful"
    return "clear"


def _p9_user_message(record: PostRecord) -> str:
    variants = [
        {"platform": v.platform.value, "title": v.title, "caption": v.caption}
        for v in record.variants
    ]
    return (
        f"<title>{record.draft.title}</title>\n"
        f"<script>{record.draft.script}</script>\n"
        f"<caption>{record.draft.caption}</caption>\n"
        f"<on_screen_text>{record.draft.on_screen_text}</on_screen_text>\n"
        f"<thumbnail_text>{record.draft.thumbnail_text}</thumbnail_text>\n"
        f"<variants>{variants}</variants>\n"
        f"<channel>{record.channel_id.value}</channel>"
    )


def is_paid(record: PostRecord) -> bool:
    if record.sponsor is not None or record.brief.sponsor_id:
        return True
    text = " ".join([record.draft.title, record.draft.caption, record.draft.script]).lower()
    return any(m in text for m in AFFILIATE_MARKERS)


def ad_prefix_ok(record: PostRecord) -> bool:
    """When paid, every variant's title and caption start with the Ad token."""
    if not record.variants:
        return record.draft.title.startswith(AD_PREFIX) and record.draft.caption.startswith(
            AD_PREFIX
        )
    return all(
        v.title.startswith(AD_PREFIX) and v.caption.startswith(AD_PREFIX) for v in record.variants
    )


def check_disclosure(record: PostRecord, client: ModelClient) -> DisclosureGate:
    gate = DisclosureGate(ai_label=True)
    gate.paid_promo = is_paid(record)
    gate.ad_prefix_ok = ad_prefix_ok(record) if gate.paid_promo else True

    result = client.run(
        spec("P9"), _p9_user_message(record), P9Output, static_blocks=[mfk_factor_block()]
    )
    unscored = result.output is None or result.output.status == "blocked"
    factors: list[tuple[str, str]] = []
    if not unscored:
        assert result.output is not None
        for f in result.output.factors:
            if f.factor_id not in MFK_FACTOR_WEIGHTS:
                unscored = True  # unknown id: fail closed, never drop it
                gate.mfk_factors.append(f"unknown_factor:{f.factor_id}")
                break
            factors.append((f.factor_id, f.outcome))
    if unscored:
        # no score means Hemant decides; the gate does not pass
        gate.made_for_kids = False
        gate.mfk_score = 1.0
        gate.mfk_band = "unscored"
        if not gate.mfk_factors:
            gate.mfk_factors = ["p9_unavailable"]
        gate.passed = False
        return gate
    gate.mfk_score = round(mfk_score(factors), 3)
    gate.mfk_factors = [f"{fid}:{out}" for fid, out in factors if out != "absent"]
    band = mfk_band(gate.mfk_score, factors)
    gate.mfk_band = band
    gate.made_for_kids = band == "likely"
    # Children are excluded as a target audience (D12): likely fails, doubtful parks for
    # Hemant (the pipeline reads mfk_band), clear passes if the ad prefix is in order.
    gate.passed = band == "clear" and gate.ad_prefix_ok
    return gate
