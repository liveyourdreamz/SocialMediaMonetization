"""What the four gates record on a post (PRD 4.12 to 4.15): originality, ethics,
disclosure and facts. One rule decides what they mean together (cap.domain.verdict)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from cap.schema.base import Strict
from cap.schema.enums import ClaimStatus, Platform, RuleOutcome, Verdict


class OriginalityGate(Strict):
    max_similarity: float = 0.0
    nearest_post_id: str | None = None
    anchor_present: bool = False
    skeleton_share: float = 0.0
    passed: bool = Field(default=False, alias="pass")


class RuleFinding(Strict):
    rule_id: str
    outcome: RuleOutcome
    evidence_span: str = ""


class JudgeResult(Strict):
    """One judge's view of one variant. A and B are the Phase 1 Claude judges; C is the
    Phase 2 cross-family judge (D32) and the majority-of-three fallback."""

    judge: Literal["A", "B", "C"]
    model: str  # the model id the API reported
    platform: Platform = Platform.YOUTUBE_LONG
    verdict: Verdict = Verdict.ESCALATE
    rule_findings: list[RuleFinding] = Field(default_factory=list)
    explanation: str = ""
    status: Literal["ok", "blocked", "refused", "timeout"] = "ok"


class EthicsGate(Strict):
    verdict: Verdict = Verdict.ESCALATE  # safe default: never a pass by omission
    rule_ids: list[str] = Field(default_factory=list)
    evidence_spans: list[str] = Field(default_factory=list)
    hard_rule_findings: list[RuleFinding] = Field(default_factory=list)
    judges: list[JudgeResult] = Field(default_factory=list)
    disagreement: bool = False


class DisclosureGate(Strict):
    ai_label: bool = True
    paid_promo: bool = False
    ad_prefix_ok: bool = True  # when paid: title and caption start with "Ad", per variant
    made_for_kids: bool = False
    mfk_score: float = 0.0
    mfk_band: Literal["clear", "doubtful", "likely", "unscored"] = "unscored"
    mfk_factors: list[str] = Field(default_factory=list)
    passed: bool = Field(default=False, alias="pass")


class ClaimCheckResult(Strict):
    """P10 per claim: both verifiers' statuses, the quote check and the final status."""

    claim_text: str
    source_url: str | None = None
    status_b: ClaimStatus = "pending"
    status_c: ClaimStatus = "pending"
    quote_in_source: bool = False
    disagreement: bool = False
    final: ClaimStatus = "pending"
    note: str = ""


class FactsGate(Strict):
    unsupported_claims: list[str] = Field(default_factory=list)
    checked: int = 0
    claim_checks: list[ClaimCheckResult] = Field(default_factory=list)
    endorsement_wording: list[str] = Field(default_factory=list)
    passed: bool = Field(default=False, alias="pass")


class Gates(Strict):
    originality: OriginalityGate = Field(default_factory=OriginalityGate)
    ethics: EthicsGate = Field(default_factory=EthicsGate)
    disclosure: DisclosureGate = Field(default_factory=DisclosureGate)
    facts: FactsGate = Field(default_factory=FactsGate)
