"""Gate 9: disclosure and labelling (PRD 4.13, Prompt Pack P9, D24).

* ai_label: always true for Patch and Falak (disclosed AI characters, D30); realistic
  synthetic media is labelled by rule R-06 regardless.
* paid_promo: true whenever the brief carries a sponsor or the draft has an affiliate link.
* made_for_kids: P9 returns factors only; code turns factors into a score with fixed
  weights and decides clear / doubtful / likely. Doubtful never auto-decides: Hemant
  decides on the card (PRD agent 9 "does not decide Made for Kids alone when doubtful").
"""

from __future__ import annotations

from collections.abc import Sequence

from cap.agents.outputs import P9Output
from cap.models import ModelClient
from cap.prompts import spec
from cap.schema.post_record import DisclosureGate, PostRecord

# COPPA / YouTube MFK factors with weights. Code constants; changed only with a signed
# decision. The P9 prompt names these factor ids.
MFK_FACTOR_WEIGHTS: dict[str, float] = {
    "child_subject_matter": 0.30,
    "child_characters_or_toys": 0.20,
    "child_oriented_language": 0.15,
    "child_oriented_music_or_animation": 0.10,
    "child_celebrities_or_influencers": 0.10,
    "intended_audience_children": 0.30,
    "adult_subject_matter": -0.35,
    "adult_language_or_complexity": -0.25,
}

MFK_LIKELY = 0.60
MFK_DOUBTFUL = 0.30


def mfk_score(factors: Sequence[tuple[str, str]]) -> float:
    """factors: (factor_id, outcome) with outcome in present/absent/unclear.
    unclear counts half. Result clipped to [0, 1]."""
    score = 0.0
    for fid, outcome in factors:
        w = MFK_FACTOR_WEIGHTS.get(fid)
        if w is None:
            continue
        if outcome == "present":
            score += w
        elif outcome == "unclear":
            score += w / 2
    return max(0.0, min(1.0, score))


def mfk_band(score: float) -> str:
    if score >= MFK_LIKELY:
        return "likely"
    if score >= MFK_DOUBTFUL:
        return "doubtful"
    return "clear"


def _p9_user_message(record: PostRecord) -> str:
    return (
        f"<title>{record.draft.title}</title>\n"
        f"<script>{record.draft.script}</script>\n"
        f"<caption>{record.draft.caption}</caption>\n"
        f"<channel>{record.channel_id.value}</channel>\n"
        f"<factor_ids>{', '.join(MFK_FACTOR_WEIGHTS)}</factor_ids>"
    )


def check_disclosure(record: PostRecord, client: ModelClient) -> DisclosureGate:
    gate = DisclosureGate(ai_label=True)
    affiliate = any(tok in record.draft.caption.lower() for tok in ("affiliate", "ref=", "?tag="))
    gate.paid_promo = bool(record.brief.sponsor_id) or affiliate

    result = client.run(spec("P9"), _p9_user_message(record), P9Output)
    if result.output is None or result.output.status == "blocked":
        # fail closed: no score means Hemant decides; the gate does not pass
        gate.made_for_kids = False
        gate.mfk_score = 1.0
        gate.mfk_factors = ["p9_unavailable"]
        gate.passed = False
        return gate
    factors = [(f.factor_id, f.outcome) for f in result.output.factors]
    gate.mfk_score = round(mfk_score(factors), 3)
    gate.mfk_factors = [f"{fid}:{out}" for fid, out in factors if out != "absent"]
    band = mfk_band(gate.mfk_score)
    gate.made_for_kids = band == "likely"
    # Children are excluded as a target audience (D12): likely is a fail, doubtful parks
    # for Hemant, clear passes.
    gate.passed = band == "clear"
    return gate
