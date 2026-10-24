"""Output schemas for the model-backed prompts (Prompt Pack v1.2, one per prompt).

Each is an Envelope subclass; the model client validates the raw JSON against it.
Numeric fields are typed but carry no min/max constraints (Claude structured-output
form); range checks are done in code after validation.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from cap.models import Envelope
from cap.schema.post_record import PlantedFault, RuleOutcome, Strict, Verdict


class RuleFindingOut(Strict):
    rule_id: str
    outcome: RuleOutcome
    evidence_span: str = ""


# ---- P4 Trend research (Pack v1.2, A17) ------------------------------------------------
class P4Item(Strict):
    topic: str
    angle: str | None = None  # stated only as far as fetched evidence goes
    demand_ref: str | None = None  # a code-measured demand signal, never the model's estimate
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_urls: list[str] = Field(default_factory=list)  # checked against the allow-list
    first_seen: str = ""
    timing: Literal["evergreen", "timely"] = "evergreen"
    is_duplicate_of: str | None = None
    source_gap: bool = False
    injection_suspected: bool = False
    explanation: str = ""


class P4Output(Envelope):
    items: list[P4Item] = Field(default_factory=list)


# ---- P5 Idea scoring (Pack v1.2, A18) -------------------------------------------------
class P5Item(Strict):
    ethics_precheck: Literal["pass", "flag", "red"]
    red_reason: str | None = None
    evidence_check: Literal["ok", "thin", "promotional"] | None = None
    constructive_fit: float | None = None  # 0 to 1, range checked in code
    persona_fit: float | None = None
    anchor_fit: float | None = None
    risk_lane: Literal["low", "high"] = "low"
    explanation: str = ""


class P5Output(Envelope):
    items: list[P5Item] = Field(default_factory=list)


# ---- P1 Ethics judge --------------------------------------------------------------
class P1Output(Envelope):
    verdict: Verdict = Verdict.ESCALATE
    rule_findings: list[RuleFindingOut] = Field(default_factory=list)


# ---- P2 Drafter -------------------------------------------------------------------
class DraftOut(Strict):
    title: str
    script: str
    shot_list: list[str] = Field(default_factory=list)
    caption: str = ""
    on_screen_text: list[str] = Field(default_factory=list)
    thumbnail_text: str = ""


class P2Call1Output(Envelope):
    draft: DraftOut | None = None
    unsupported_requests: list[str] = Field(default_factory=list)


class ClaimOut(Strict):
    text: str
    source_url: str | None = None
    source_quote: str | None = None
    in_test_log: bool = False


class P2Call2Output(Envelope):
    claims: list[ClaimOut] = Field(default_factory=list)
    numeral_check: list[str] = Field(default_factory=list)


# ---- P3 Critic --------------------------------------------------------------------
class IssueOut(Strict):
    rubric_item: str
    severity: Literal["blocker", "major", "minor"]  # P3 instruction 3 depends on these names
    span: str = ""
    instruction: str = ""


class P3Output(Envelope):
    verdict: Literal["accept", "revise"] = "revise"
    issues: list[IssueOut] = Field(default_factory=list)
    estimated_length_s: int = 0


# ---- P7 Platform adapter ----------------------------------------------------------
class VariantOut(Strict):
    platform: Literal["youtube_long", "youtube_short", "facebook", "instagram"]
    title: str
    caption: str = ""
    script_cut: str = ""
    duration_s: int = 0
    on_screen_text: list[str] = Field(default_factory=list)
    thumbnail_text: str = ""
    disclosure_preserved: bool = False


class P7Output(Envelope):
    variants: list[VariantOut] = Field(default_factory=list)


# ---- P8 Originality failure explainer ---------------------------------------------
class P8Output(Envelope):
    matched_passages: list[list[str]] = Field(default_factory=list)
    suggested_change: str | None = None


# ---- P9 Made for Kids scorer (factors only; code scores, D24) ----------------------
class MfkFactor(Strict):
    factor_id: str
    outcome: Literal["present", "absent", "unclear"]
    strength: Literal["weak", "moderate", "strong"] = "moderate"
    evidence_span: str = ""


class P9Output(Envelope):
    factors: list[MfkFactor] = Field(default_factory=list)
    intended_audience_note: str = ""


# ---- P10 Fact and claim check -----------------------------------------------------
class P10ClaimOut(Strict):
    text: str
    source_url: str | None = None


class P10CallAOutput(Envelope):
    claims: list[P10ClaimOut] = Field(default_factory=list)
    added_claims: list[str] = Field(default_factory=list)
    endorsement_wording: list[str] = Field(default_factory=list)


class P10VerifyOutput(Envelope):
    """Calls B and C share this shape. The Pack names this field `status`, which
    collides with the envelope's `status`; logged as a Pack 1.1.2 amendment, the
    field is `claim_status` here and in the prompt text sent to the model."""

    claim_status: Literal["supported", "unsupported", "outdated", "missing_source"] = "unsupported"
    supporting_quote: str = ""
    note: str = ""


# ---- P14 Canary writer ------------------------------------------------------------
class P14Output(Envelope):
    canary_draft: DraftOut | None = None
    planted_fault: PlantedFault | None = None
