"""The shared post record: the data contract every agent reads and writes (PRD 4.1).

Pydantic is the single definition; `export_json_schema()` writes the record's JSON
schema for the database and the approval app. (The models' own output schemas are the
Envelope subclasses in cap.agents.outputs, not this file.) Field names follow the PRD;
the one exception is PRD `pass`, a Python keyword, stored as `passed` with the JSON
alias `pass` so the record on disk still reads as the PRD does.

Updated for the decisions after PRD v1.0:
* D33: approval stores a pre-reveal and a post-reveal verdict.
* D34: approval carries a reason chip and a spot-check result.
* D23: a canary flag that the publisher refuses and the history index skips.
* Review C1/C2: a release sub-record holds the final render (the approver's opening
  line spoken), its manifest hash and its state, apart from the review render.
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
    """Base with additionalProperties=false, as the structured-output rules require.
    It is shared with the model-output envelopes (cap.agents.outputs), so it stays
    strict: records stored on disk are read through cap.schema.migrate.load_record,
    which tolerates fields an older or newer build wrote."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, validate_assignment=True)


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


class FaultType(StrEnum):
    """The seven canary fault types (Prompt Pack P14). A catch counts only when the
    reviewer's reason chip matches the planted type, so the chips use the same values."""

    UNSOURCED_NUMBER = "unsourced_number"
    MISSING_AD_PREFIX = "missing_ad_prefix"
    OUT_GROUP_TITLE = "out_group_title"
    FIRST_PERSON_HUMAN_CLAIM = "first_person_human_claim"
    SPONSOR_VERDICT = "sponsor_verdict"
    KIDS_STYLED_VISUAL = "kids_styled_visual"
    REUSED_COLD_OPEN = "reused_cold_open"


# D34 reason chips: the seven fault types, language, other. Fixed list; "other" above
# 20% of a week's rejects triggers a review of this list, not a free-text field.
class ReasonCode(StrEnum):
    UNSOURCED_NUMBER = "unsourced_number"
    MISSING_AD_PREFIX = "missing_ad_prefix"
    OUT_GROUP_TITLE = "out_group_title"
    FIRST_PERSON_HUMAN_CLAIM = "first_person_human_claim"
    SPONSOR_VERDICT = "sponsor_verdict"
    KIDS_STYLED_VISUAL = "kids_styled_visual"
    REUSED_COLD_OPEN = "reused_cold_open"
    LANGUAGE = "language"
    OTHER = "other"


def _pack_version() -> str:
    from cap.prompts import pack_version

    return pack_version()


class Versions(Strict):
    prompts: str = Field(default_factory=_pack_version)
    rulebook: str = "1.0"
    anchor_registry: str = "1.0"


class Brief(Strict):
    topic: str = ""
    fast_moving: bool = False  # P10 step 5: sources older than 12 months are outdated
    format_id: str = ""
    anchor_elements: list[str] = Field(default_factory=list)
    claims_allowed: list[str] = Field(default_factory=list)
    length_s: int = 600
    lane: Lane = Lane.LOW
    sponsor_id: str | None = None


ClaimStatus = Literal["pending", "supported", "unsupported", "outdated", "missing_source"]


class Claim(Strict):
    text: str
    source_url: str | None = None
    source_quote: str | None = None
    in_test_log: bool = False  # P2 call 2: the claim comes from the pipeline's own test
    status: ClaimStatus = "pending"


class Sponsor(Strict):
    """The sponsor record P1, P6 and P9 receive. Null on the post when unsponsored."""

    sponsor_id: str
    name: str
    permit_status: Literal["none", "applied", "granted"] = "none"
    nma_category_status: Literal["unknown", "allowed", "restricted"] = "unknown"
    verdict_allowed: bool = False  # R-10: never a Keep/Recommend/Skip on their product


class MediaManifest(Strict):
    """What the renderer will produce or did produce; the judges read it for R-06."""

    synthetic_elements_present: bool = True  # Patch and Falak are AI characters (D30)
    real_person_depicted: bool = False
    real_place_or_event_depicted: bool = False
    minor_depicted: bool = False
    crisis_event_depicted: bool = False
    third_party_footage_share: float = 0.0
    imagery_roles: list[str] = Field(default_factory=list)  # people in occupational roles (R-14a)


class CriticIssue(Strict):
    rubric_item: str
    severity: Literal["blocker", "major", "minor"]
    span: str = ""
    instruction: str = ""


class Draft(Strict):
    title: str = ""
    script: str = ""
    shot_list: list[str] = Field(default_factory=list)
    caption: str = ""
    on_screen_text: list[str] = Field(default_factory=list)
    thumbnail_text: str = ""
    claims: list[Claim] = Field(default_factory=list)
    source_list: list[str] = Field(default_factory=list)  # R-12: URLs for the description
    critic_issues: list[CriticIssue] = Field(default_factory=list)  # PRD 4.10, revealed D33
    critic_verdict: Literal["", "accept", "revise"] = ""


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
    on_screen_text: list[str] = Field(default_factory=list)
    thumbnail_text: str = ""


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


class Asset(Strict):
    """The REVIEW render: what the card plays, with the opening line as a visible
    placeholder. What is published is the final render in `Release`."""

    uri: str = ""  # the long-form review file
    sha256: str = ""
    c2pa_manifest_uri: str = ""
    short_uri: str = ""
    thumbnail_uri: str = ""
    duration_s: float = 0.0
    voice: str = ""  # the voice id used (edge-tts now, ElevenLabs later)
    kind: Literal["text", "video"] = "text"


class ReleaseStatus(StrEnum):
    NONE = "none"  # not approved yet, or approved before releases existed
    PREPARING = "preparing"  # the final render is running
    READY = "ready"  # final files, manifest and token in place
    FAILED = "failed"  # the final render or the token issue failed; `error` says why


class ReleaseFile(Strict):
    role: Literal["long", "short", "thumbnail"]
    uri: str
    sha256: str  # at render time; the publisher re-hashes the file on disk


class Release(Strict):
    """The final package: rendered after approval with the opening line spoken and
    shown (review C1). The approval token binds `manifest_sha256`, the hash of the
    canonical manifest of these files and every text and flag that goes out (C2)."""

    status: ReleaseStatus = ReleaseStatus.NONE
    files: list[ReleaseFile] = Field(default_factory=list)
    manifest_sha256: str = ""
    error: str = ""
    attempts: int = 0
    prepared_at: datetime | None = None


class SpotCheck(Strict):
    """D34: one claim chosen at random, shown with its source quote."""

    claim_text: str
    reviewer_said_supported: bool | None = None
    actually_supported: bool | None = None
    is_planted: bool = False  # a canary's planted claim; a wrong tap is logged as a miss


class StageOneReview(Strict):
    """D19: channel C is reviewed by the native Arabic reviewer first; Hemant sees an
    English summary (P11) plus the reviewer's notes."""

    reviewer: str
    decision: Decision
    notes: str = ""
    time_on_card_s: int = 0


class Approval(Strict):
    decision: Decision | None = None
    reason_code: ReasonCode | None = None
    reviewer: str = "hemant"
    time_on_card_s: int = 0
    expected_reading_s: int = 0  # D34: per-card threshold, not a fixed 20 s
    too_fast: bool = False
    # D33: the verdict recorded before gate outcomes were revealed, and the final one.
    pre_reveal_decision: Decision | None = None
    pre_reveal_reason_code: ReasonCode | None = None  # P14 scores canaries on these two
    revised_after_reveal: bool = False
    decided_at: datetime | None = None  # when finalise closed the post
    spot_check: SpotCheck | None = None
    edits: list[str] = Field(default_factory=list)  # PRD 4.5: reviewer edits as a diff log
    stage_one: StageOneReview | None = None
    token: str = ""
    # D43: the needs_decision items the approver reviewed and accepted after the reveal
    decisions_acknowledged: list[str] = Field(default_factory=list)


class Publish(Strict):
    platform_post_ids: dict[str, str] = Field(default_factory=dict)
    published_at: datetime | None = None
    urls: dict[str, str] = Field(default_factory=dict)  # destination -> where to see it
    errors: dict[str, str] = Field(default_factory=dict)  # destination -> why it did not go
    destinations: list[str] = Field(default_factory=list)  # what the approver chose


class Metrics(Strict):
    views: int = 0
    avg_view_pct: float = 0.0
    rpm_usd: float = 0.0
    revenue_usd: float = 0.0
    cost_usd: float = 0.0
    revenue_status: Literal["none", "estimated", "adjusted", "finalised"] = "none"  # P12


class PlantedFault(Strict):
    type: FaultType
    span: str
    why_catchable: str = ""


class Flags(Strict):
    injection_suspected: bool = False
    injection_spans: list[str] = Field(default_factory=list)
    canary: bool = False  # D23: set by code; the publisher refuses, the index skips
    canary_fault_type: FaultType | None = None
    planted_fault: PlantedFault | None = None  # revealed on the card after the verdict
    canary_outcome: Literal["", "caught", "missed"] = ""
    gate_blind: bool = True  # D33: gates hidden on the card until a verdict is recorded
    card_prepared: bool = False  # spot check and reading time set when queued (D34)
    tour: bool = False  # the guided tour's worked example; never counted, never published
    # where the post stopped (a workflow step key) and why, written when it stops
    stopped_at: str = ""
    stop_reason: str = ""
    # D43: questions a gate could not settle (an ethics escalation, a made-for-kids
    # doubt). The post goes on to the card; Hemant settles each one there.
    needs_decision: list[str] = Field(default_factory=list)


# The stored record's layout version. Raise it when a change needs old files rewritten,
# and add the step to cap.schema.migrate.MIGRATIONS (one function per version step).
SCHEMA_VERSION = 2


class PostRecord(Strict):
    schema_version: int = SCHEMA_VERSION
    post_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    channel_id: Channel
    status: PostStatus = PostStatus.BRIEFED
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    versions: Versions = Field(default_factory=Versions)
    brief: Brief = Field(default_factory=Brief)
    sponsor: Sponsor | None = None
    draft: Draft = Field(default_factory=Draft)
    media_manifest: MediaManifest = Field(default_factory=MediaManifest)
    human_contribution: HumanContribution = Field(default_factory=HumanContribution)
    variants: list[Variant] = Field(default_factory=list)
    gates: Gates = Field(default_factory=Gates)
    asset: Asset = Field(default_factory=Asset)
    release: Release = Field(default_factory=Release)
    approval: Approval = Field(default_factory=Approval)
    publish: Publish = Field(default_factory=Publish)
    metrics: Metrics = Field(default_factory=Metrics)
    flags: Flags = Field(default_factory=Flags)
    history: list[str] = Field(default_factory=list)  # audit trail lines, append only

    @property
    def gates_cleared(self) -> bool:
        """Publishable as far as the gates go (cap.gates.verdict owns the rule): no gate
        failed outright, and every question on the card was acknowledged (D43, R-20)."""
        from cap.gates.verdict import gates_cleared

        return gates_cleared(self)

    def stop(self, step: str, reason: str, status: PostStatus | None = None) -> None:
        """Record where and why the post stopped; the workflow bar reads these."""
        self.flags.stopped_at = step
        self.flags.stop_reason = reason
        if status is not None:
            self.status = status

    def log(self, event: str) -> None:
        self.history.append(f"{datetime.now(UTC).isoformat()} {event}")


SCHEMA_PATH = Path(__file__).with_name("post_record.schema.json")


def export_json_schema(path: Path = SCHEMA_PATH) -> Path:
    """Write the record's JSON schema (by alias, so `pass` appears as in the PRD).
    Run via `cap schema export`; CI checks the file matches the code."""
    schema = PostRecord.model_json_schema(by_alias=True)
    path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    return path
