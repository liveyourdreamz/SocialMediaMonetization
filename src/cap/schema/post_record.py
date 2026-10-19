"""The shared post record: the data contract every agent reads and writes (PRD 4.1).

Pydantic is the single definition; `export_json_schema()` writes the canonical JSON
schema that the model prompts validate against (D28). Field names follow the PRD
exactly so the PRD, the prompts and the code say the same thing.

Updated for the decisions after PRD v1.0:
* D33: approval stores a pre-reveal and a post-reveal verdict.
* D34: approval carries a reason chip and a spot-check result.
* D23: a canary flag that the publisher refuses and the history index skips.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    """Base with additionalProperties=false, as the structured-output rules require."""

    model_config = ConfigDict(extra="forbid")


class Channel(StrEnum):
    A = "A"  # Patch, English, AI tools
    B = "B"  # Falak, English, science
    C = "C"  # Falak, Arabic, science


class PostStatus(StrEnum):
    BRIEFED = "briefed"
    DRAFTING = "drafting"
    GATED = "gated"
    RENDERING = "rendering"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    PUBLISHED = "published"
    REJECTED = "rejected"
    DISCARDED = "discarded"
    PARKED = "parked"  # an injection flag or a gate escalation parks; a human decides


class Platform(StrEnum):
    YOUTUBE_LONG = "youtube_long"
    YOUTUBE_SHORT = "youtube_short"
    FACEBOOK = "facebook"
    INSTAGRAM = "instagram"


class Lane(StrEnum):
    LOW = "low"
    HIGH = "high"


class Verdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    ESCALATE = "escalate"


class RuleOutcome(StrEnum):
    NOT_TRIGGERED = "not_triggered"
    REVISE = "revise"
    ESCALATE = "escalate"
    BLOCK = "block"


class Decision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    ESCALATE = "escalate"


# D34 reason chips: the seven fault types, language, other. Fixed list; "other" above
# 20% of a week's rejects triggers a review of this list, not a free-text field.
class ReasonCode(StrEnum):
    FACT = "fact"
    ETHICS = "ethics"
    DISCLOSURE = "disclosure"
    ORIGINALITY = "originality"
    TITLE_MISMATCH = "title_mismatch"
    CHARACTER_BREAK = "character_break"
    SPONSOR = "sponsor"
    LANGUAGE = "language"
    OTHER = "other"


class Versions(Strict):
    prompts: str = "1.1.1"
    rulebook: str = "1.0"
    anchor_registry: str = "1.0"


class Brief(Strict):
    topic: str = ""
    format_id: str = ""
    anchor_elements: list[str] = Field(default_factory=list)
    claims_allowed: list[str] = Field(default_factory=list)
    length_s: int = 600
    lane: Lane = Lane.LOW
    sponsor_id: str | None = None


class Claim(Strict):
    text: str
    source_url: str | None = None
    source_quote: str | None = None
    status: Literal["pending", "supported", "unsupported", "outdated", "missing_source"] = "pending"


class Draft(Strict):
    title: str = ""
    script: str = ""
    shot_list: list[str] = Field(default_factory=list)
    caption: str = ""
    claims: list[Claim] = Field(default_factory=list)


class HumanContribution(Strict):
    type: Literal["opening_line", "commentary", "format_decision"] = "opening_line"
    text: str = ""
    author: str = "hemant"


class Variant(Strict):
    platform: Platform
    title: str = ""
    caption: str = ""
    duration_s: int = 0
    script_cut: str = ""


class OriginalityGate(Strict):
    max_similarity: float = 0.0
    nearest_post_id: str | None = None
    anchor_present: bool = False
    skeleton_share: float = 0.0
    passed: bool = False  # PRD calls this `pass`; a Python keyword, so `passed` here


class RuleFinding(Strict):
    rule_id: str
    outcome: RuleOutcome
    evidence_span: str = ""


class JudgeResult(Strict):
    judge: Literal["A", "B", "C"]
    model: str
    verdict: Verdict
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
    made_for_kids: bool = False
    mfk_score: float = 0.0
    mfk_factors: list[str] = Field(default_factory=list)
    passed: bool = False


class FactsGate(Strict):
    unsupported_claims: list[str] = Field(default_factory=list)
    checked: int = 0
    passed: bool = False


class Gates(Strict):
    originality: OriginalityGate = Field(default_factory=OriginalityGate)
    ethics: EthicsGate = Field(default_factory=EthicsGate)
    disclosure: DisclosureGate = Field(default_factory=DisclosureGate)
    facts: FactsGate = Field(default_factory=FactsGate)

    @property
    def all_passed(self) -> bool:
        return (
            self.originality.passed
            and self.ethics.verdict == Verdict.PASS
            and self.disclosure.passed
            and self.facts.passed
        )


class Asset(Strict):
    uri: str = ""
    sha256: str = ""
    c2pa_manifest_uri: str = ""


class SpotCheck(Strict):
    """D34: one claim chosen at random, shown with its source quote."""

    claim_text: str
    reviewer_said_supported: bool | None = None
    actually_supported: bool | None = None
    is_planted: bool = False  # a canary's planted claim; a wrong tap is logged as a miss


class Approval(Strict):
    decision: Decision | None = None
    reason_code: ReasonCode | None = None
    reviewer: str = "hemant"
    time_on_card_s: int = 0
    expected_reading_s: int = 0  # D34: per-card threshold, not a fixed 20 s
    too_fast: bool = False
    canary: bool = False
    # D33: the verdict recorded before gate outcomes were revealed, and the final one.
    pre_reveal_decision: Decision | None = None
    revised_after_reveal: bool = False
    spot_check: SpotCheck | None = None
    token: str = ""


class Publish(Strict):
    platform_post_ids: dict[str, str] = Field(default_factory=dict)
    published_at: datetime | None = None


class Metrics(Strict):
    views: int = 0
    avg_view_pct: float = 0.0
    rpm_usd: float = 0.0
    revenue_usd: float = 0.0
    cost_usd: float = 0.0
    revenue_status: Literal["none", "provisional", "finalised"] = "none"


class Flags(Strict):
    injection_suspected: bool = False
    injection_spans: list[str] = Field(default_factory=list)
    canary: bool = False  # D23: set by code; the publisher refuses, the index skips
    canary_fault_type: str | None = None
    gate_blind: bool = True  # D33: gates hidden on the card until a verdict is recorded


class PostRecord(Strict):
    post_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    channel_id: Channel
    status: PostStatus = PostStatus.BRIEFED
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    versions: Versions = Field(default_factory=Versions)
    brief: Brief = Field(default_factory=Brief)
    draft: Draft = Field(default_factory=Draft)
    human_contribution: HumanContribution = Field(default_factory=HumanContribution)
    variants: list[Variant] = Field(default_factory=list)
    gates: Gates = Field(default_factory=Gates)
    asset: Asset = Field(default_factory=Asset)
    approval: Approval = Field(default_factory=Approval)
    publish: Publish = Field(default_factory=Publish)
    metrics: Metrics = Field(default_factory=Metrics)
    flags: Flags = Field(default_factory=Flags)
    history: list[str] = Field(default_factory=list)  # audit trail lines, append only

    def log(self, event: str) -> None:
        self.history.append(f"{datetime.now(UTC).isoformat()} {event}")


SCHEMA_PATH = Path(__file__).with_name("post_record.schema.json")


def export_json_schema(path: Path = SCHEMA_PATH) -> Path:
    """Write the canonical JSON schema (D28). Run via `cap schema export`."""
    schema = PostRecord.model_json_schema()
    path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    return path
