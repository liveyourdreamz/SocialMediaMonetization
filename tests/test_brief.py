"""The brief builder's code half (PRD 4.9): format, risk lane, claims scope, sponsor."""

from __future__ import annotations

import pytest

from cap.demo.seed import new_brief
from cap.domain.brief import BriefRefusedError, build_brief, sensitive_areas
from cap.domain.card import expected_reading_seconds
from cap.post_context import post_context
from cap.schema.post_record import Lane, Sponsor


def _brief(topic: str, channel: str = "A", sponsor: Sponsor | None = None):
    return build_brief(
        channel, topic, anchor_elements=["a", "b", "c"], length_s=30, sponsor=sponsor
    )


def test_every_brief_names_a_format_and_an_anchor():
    for ch in ("A", "B", "C"):
        b = _brief("why bread goes stale", ch)
        assert b.format_id and b.anchor_elements == ["a", "b"]
    assert _brief("x", "B").format_id == "from_orbit"


@pytest.mark.parametrize(
    "topic, areas",
    [
        ("How sleep trackers work", ["health"]),  # PRD 4.9 edge example
        ("How a mortgage rate is set", ["money"]),
        ("Why toddlers need car seats", ["children"]),
        ("How an election count works", ["politics"]),
        ("كيف يعمل اللقاح", ["health"]),
        ("How monsoons form", []),
        ("Why bread goes stale", []),
        ("How banking apps encrypt data", []),  # whole words only: "banking" is not "bank"
    ],
)
def test_sensitive_topics_go_to_the_high_lane(topic, areas):
    assert sensitive_areas(topic) == areas
    b = _brief(topic)
    assert b.lane == (Lane.HIGH if areas else Lane.LOW)
    assert any("no advice" in c for c in b.claims_allowed) == bool(areas)


def test_a_sponsor_without_a_permit_or_category_is_refused():
    with pytest.raises(BriefRefusedError, match="permit is applied"):
        _brief("x", sponsor=Sponsor(sponsor_id="s1", name="Acme", permit_status="applied"))
    with pytest.raises(BriefRefusedError, match="NMA"):  # PRD 4.9 failure example
        _brief("x", sponsor=Sponsor(sponsor_id="s1", name="Acme", permit_status="granted"))


def test_a_valid_sponsor_never_gets_a_verdict():
    sp = Sponsor(
        sponsor_id="s1",
        name="Acme",
        permit_status="granted",
        nma_category_status="allowed",
        verdict_allowed=True,
    )
    b = _brief("x", sponsor=sp)
    assert b.sponsor_id == "s1" and sp.verdict_allowed is False


def test_an_empty_topic_or_unknown_channel_is_refused():
    with pytest.raises(BriefRefusedError):
        _brief("   ")
    with pytest.raises(BriefRefusedError):
        _brief("x", "Z")


def test_the_writer_is_told_the_scope_and_the_lane():
    rec = new_brief("A", "How sleep trackers work")
    ctx = post_context(rec, "writer")
    assert "risk lane: high" in ctx and "no advice, outcomes" in ctx
    assert any("high-risk lane" in line for line in rec.history)


def test_a_high_lane_card_needs_longer_reading():
    low = new_brief("A", "Why bread goes stale")
    high = new_brief("A", "How sleep trackers work")
    for r in (low, high):
        r.draft.script = "word " * 2000
    assert expected_reading_seconds(high) == int(expected_reading_seconds(low) * 1.5)
