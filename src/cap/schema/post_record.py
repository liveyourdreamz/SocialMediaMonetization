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
from pathlib import Path
from typing import Literal

from pydantic import Field

from cap.schema.base import Strict
from cap.schema.enums import (
    Channel,
    ClaimStatus,
    Decision,
    FaultType,
    Lane,
    Platform,
    PostStatus,
    ReasonCode,
    ReleaseStatus,
    RuleOutcome,
    Verdict,
)
from cap.schema.gates import (
    ClaimCheckResult,
    DisclosureGate,
    EthicsGate,
    FactsGate,
    Gates,
    JudgeResult,
    OriginalityGate,
    RuleFinding,
)


def _pack_version() -> str:
    from cap.version import pack_version

    return pack_version()


# The public names: the parts defined here and the vocabularies and gate results
# kept in cap.schema.enums and cap.schema.gates (callers import them all from here).
__all__ = [
    "Approval",
    "Asset",
    "Brief",
    "Channel",
    "Claim",
    "ClaimCheckResult",
    "ClaimStatus",
    "CriticIssue",
    "Decision",
    "DisclosureGate",
    "Draft",
    "EthicsGate",
    "FactsGate",
    "FaultType",
    "Flags",
    "Gates",
    "HumanContribution",
    "JudgeResult",
    "Lane",
    "MediaManifest",
    "Metrics",
    "OriginalityGate",
    "PlantedFault",
    "Platform",
    "PostRecord",
    "PostStatus",
    "Publish",
    "ReasonCode",
    "Release",
    "ReleaseFile",
    "ReleaseStatus",
    "RuleFinding",
    "RuleOutcome",
    "SCHEMA_PATH",
    "SCHEMA_VERSION",
    "Sponsor",
    "SpotCheck",
    "StageOneReview",
    "Strict",
    "Variant",
    "Verdict",
    "Versions",
    "export_json_schema",
]


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


class CostLine(Strict):
    """One model call charged to the post (D44; priced by cap.domain.cost)."""

    what: str  # the prompt or service, e.g. "P2 call 1", "research scout"
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    usd: float = 0.0
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))


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
    # How many times the store has saved this post. A save from a copy older than the
    # stored one is refused (cap.store.PostConflictError), so no change is lost silently.
    revision: int = Field(default=0, ge=0)
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
    costs: list[CostLine] = Field(default_factory=list)  # metrics.cost_usd is their sum
    flags: Flags = Field(default_factory=Flags)
    history: list[str] = Field(default_factory=list)  # audit trail lines, append only

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
