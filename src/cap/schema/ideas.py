"""An idea: one candidate topic shown in the Monday Digest (PRD 4.7 and 4.8, D48).

Trend research (P4) finds it, idea scoring (P5) judges it, code ranks it and the
operator picks, edits or rejects it. A picked idea becomes a brief. Ideas are kept for
the 90-day memory that stops the same topic being proposed again.
"""

from __future__ import annotations

import datetime as dt
import uuid
from enum import StrEnum
from typing import Literal

from pydantic import Field

from cap.schema.base import Strict


class IdeaStatus(StrEnum):
    SHOWN = "shown"  # in this week's Digest
    PICKED = "picked"  # became a brief
    REJECTED = "rejected"  # the operator said no
    EXPIRED = "expired"  # nobody acted by the next Monday; remembered, not shown
    HELD = "held"  # never shown: Red, thin or promotional evidence, or no primary source


DECIDED = (IdeaStatus.PICKED, IdeaStatus.REJECTED)


class Idea(Strict):
    idea_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    channel: str
    week: dt.date  # the Monday of the Digest it belongs to
    rank: int = 0  # 1 is first in the Digest; 0 when held
    status: IdeaStatus = IdeaStatus.SHOWN
    # from trend research (P4); topic and angle came from untrusted pages
    topic: str
    angle: str | None = None
    demand_title: str | None = None  # the Wikipedia article measured, when matched
    views_7d: int = 0
    ratio_to_baseline: float = 0.0
    evidence_urls: list[str] = Field(default_factory=list)
    timing: Literal["evergreen", "timely"] = "evergreen"
    source_gap: bool = False
    injection_suspected: bool = False
    # from idea scoring (P5)
    ethics_precheck: Literal["pass", "flag", "red"] = "pass"
    evidence_check: Literal["ok", "thin", "promotional"] | None = None
    constructive_fit: float | None = None
    persona_fit: float | None = None
    anchor_fit: float | None = None
    risk_lane: Literal["low", "high"] = "low"
    explanation: str = ""
    # computed by code (D24)
    score: float = 0.0
    revenue_low_usd: float = 0.0  # an assumption until the pilot has view data
    revenue_high_usd: float = 0.0
    held_reason: str = ""
    # the operator's decision
    topic_edit: str = ""
    post_id: str | None = None
    decided_at: dt.datetime | None = None
    created_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.UTC))
