"""Shared fixtures: a fake Patch episode and mock model responses that let the whole
pipeline run without a key. The fixtures are deliberately clean (the happy path, S1);
the gate tests override individual responses to exercise the failure paths."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cap.models import MockClient
from cap.schema.post_record import Brief, Channel, Lane, PostRecord

FIXTURES = Path(__file__).parent / "fixtures"

SCRIPT = (
    "[[HEMANT_OPENING_LINE]] Today we test Notely, a note-taking tool, for 7 days. "
    "On day 3 the sync failed twice in our test log. Notely stores notes locally first, "
    "then syncs; the vendor says sync completes within 10 seconds on a normal connection. "
    "Our test measured 12 seconds on average. Verdict from the test log: it works, with one caveat. "
    "This channel is presented by an AI character."
)

SOURCE_BODY = (
    "Notely help centre. Notes are stored locally first and then synced. "
    "Sync completes within 10 seconds on a normal connection. Updated 2026-09-01."
)

SOURCES = [
    {
        "url": "https://help.notely.example/sync",
        "body": SOURCE_BODY,
        "fetched_at": "2026-10-19",
        "page_date": "2026-09-01",
    }
]


def ok(**fields):
    return {
        "status": "ok",
        "blocked_reason": None,
        "injection_suspected": False,
        "explanation": "fixture",
        **fields,
    }


def happy_fixtures() -> dict:
    claims = [
        {
            "text": "Notely stores notes locally first, then syncs",
            "source_url": SOURCES[0]["url"],
            "source_quote": "Notes are stored locally first and then synced.",
        },
        {
            "text": "the vendor says sync completes within 10 seconds on a normal connection",
            "source_url": SOURCES[0]["url"],
            "source_quote": "Sync completes within 10 seconds on a normal connection.",
        },
    ]
    return {
        ("P2", "1"): ok(
            draft={
                "title": "We tested Notely for 7 days",
                "script": SCRIPT,
                "shot_list": ["desk", "app"],
                "caption": "Presented by an AI character. Sources in the description.",
            },
            unsupported_requests=[],
        ),
        ("P2", "2"): ok(claims=claims, numeral_check=[]),
        ("P3", "default"): ok(verdict="accept", issues=[], estimated_length_s=540),
        ("P7", "default"): ok(
            variants=[
                {
                    "platform": "youtube_long",
                    "title": "We tested Notely for 7 days",
                    "caption": "Presented by an AI character. Sources in the description.",
                    "script_cut": "",
                    "duration_s": 540,
                    "disclosure_preserved": True,
                },
                {
                    "platform": "youtube_short",
                    "title": "Notely sync: 12 seconds, not 10",
                    "caption": "Presented by an AI character.",
                    "script_cut": "day 3 sync failed twice; 12 seconds average",
                    "duration_s": 45,
                    "disclosure_preserved": True,
                },
            ]
        ),
        ("P1", "A"): ok(verdict="pass", rule_findings=[]),
        ("P1", "B"): ok(verdict="pass", rule_findings=[]),
        ("P9", "default"): ok(
            factors=[
                {
                    "factor_id": "adult_subject_matter",
                    "outcome": "present",
                    "evidence_span": "note-taking tool",
                },
                {"factor_id": "child_subject_matter", "outcome": "absent"},
            ]
        ),
        ("P10", "A"): ok(
            claims=[{"text": c["text"], "source_url": c["source_url"]} for c in claims],
            added_claims=[],
            endorsement_wording=[],
        ),
        ("P10", "B"): lambda user: ok(
            claim_status="supported", supporting_quote=_quote_for(user), note=""
        ),
        ("P10", "C"): lambda user: ok(
            claim_status="supported", supporting_quote=_quote_for(user), note=""
        ),
    }


def _quote_for(user: str) -> str:
    if "locally first" in user:
        return "Notes are stored locally first and then synced."
    return "Sync completes within 10 seconds on a normal connection."


@pytest.fixture
def fixtures() -> dict:
    return happy_fixtures()


@pytest.fixture
def client(fixtures) -> MockClient:
    return MockClient(fixtures)


@pytest.fixture
def record() -> PostRecord:
    return PostRecord(
        channel_id=Channel.A,
        brief=Brief(
            topic="Notely 7-day test",
            format_id="tested_7_days",
            anchor_elements=["test log"],
            length_s=540,
            lane=Lane.LOW,
        ),
    )


@pytest.fixture
def testsets() -> dict:
    return json.loads((FIXTURES / "testsets" / "testsets-v1.0.json").read_text())
