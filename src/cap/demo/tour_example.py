"""The guided tour's worked example: Patch tests Driftpad, an AI meeting-notes tool,
for seven days, in the "Tested for 7 days" format (hook, setup, three real tasks with
logged timings, what broke, the verdict stamp).

Driftpad is fictional, and the tour says so on its first stop: a real product would
need a real test, and every number Patch speaks must trace to a log or a cited page.
Here the test log and the two "vendor" pages are written for the tour, so each claim
is checkable end to end without naming anyone's product. The critic sends the first
draft back once (the verdict came before the evidence), so the tour shows the
revision loop too.
"""

from __future__ import annotations

import re

from cap.demo import ok
from cap.policy import AI_ROLE_DISCLOSURE

TOOL = "Driftpad"
TOPIC = "Driftpad 7-day test: AI meeting notes"

TEST_LOG = (
    "Patch test log, Driftpad, 7 days, version 2.4.0 then 2.4.1. "
    "Task one: a 30-minute recorded call. The transcript took 58 seconds. "
    "Task two: action items from the same call. It found 11 of the 14 action items I had "
    "written down by hand, and invented 2 that nobody said. "
    "Task three: export the notes to a document. On day 5 the export failed twice. "
    "Version 2.4.1 arrived on day 6 and the export worked on day 6. "
    "Transcript quality was good throughout: speaker names right in every call."
)

HELP_URL = "https://help.driftpad.example/speed"
CHANGELOG_URL = "https://help.driftpad.example/changelog"

SOURCES = [
    {
        "url": HELP_URL,
        "body": (
            "Driftpad help centre: how fast is it? Driftpad turns a meeting into notes and "
            "action items while you are still saying goodbye. Transcripts are ready in under "
            "30 seconds for a 30-minute meeting. Action items are extracted automatically "
            "from what was said. Updated 2026-09-14."
        ),
        "fetched_at": "2026-10-21",
        "page_date": "2026-09-14",
    },
    {
        "url": CHANGELOG_URL,
        "body": (
            "Driftpad changelog. Version 2.4.1, released 2026-10-06: fixes export to "
            "documents failing on large transcripts. Version 2.4.0: new action-item view."
        ),
        "fetched_at": "2026-10-21",
        "page_date": "2026-10-06",
    },
]

CAPTION = f"A seven-day test from Patch's test log; every number is in the description. {AI_ROLE_DISCLOSURE}"

# The first draft: the verdict arrives before the evidence; the critic sends it back.
SCRIPT_V1 = (
    "[[HEMANT_OPENING_LINE]] I gave Driftpad seven days. Verdict first: Watch. "
    "Driftpad says it turns a meeting into notes and action items while you are still "
    "saying goodbye. Task one: a 30-minute call. The transcript took 58 seconds in my test "
    "log; the help page says under 30 seconds. Task two: action items. It found 11 of the "
    "14 I had written down, and invented 2 that nobody said. Task three: export to a "
    "document. On day 5 the export failed twice; version 2.4.1 fixed it on day 6."
)

# The revision: evidence first, verdict last, one catchphrase.
SCRIPT = (
    "[[HEMANT_OPENING_LINE]] I gave Driftpad seven days.\n\n"
    "Driftpad says it turns a meeting into notes and action items while you are still "
    "saying goodbye. That is the claim, so that is the test.\n\n"
    "Task one: a 30-minute call. The transcript took 58 seconds in my test log; the help "
    "page says under 30 seconds. Good transcript, speaker names right every time, but "
    "nearly twice the promised wait.\n\n"
    "Task two: action items. It found 11 of the 14 I had written down, and invented 2 "
    "that nobody said. Two invented tasks in one meeting is two too many.\n\n"
    "Task three: export to a document. On day 5 the export failed twice; version 2.4.1 "
    "fixed it on day 6, and the changelog says why.\n\n"
    "Verdict from the test log: Watch. A good transcript, slower than promised, and it "
    "makes things up. Check back after the next version."
)

CRITIC_ISSUES = [
    {
        "rubric_item": "format beats",
        "severity": "major",
        "span": "Verdict first: Watch.",
        "instruction": (
            "The verdict stamp closes the episode; it cannot come before the three tasks. "
            "Move it to the last beat and let the evidence lead."
        ),
    },
    {
        "rubric_item": "character voice",
        "severity": "minor",
        "span": "version 2.4.1 fixed it on day 6.",
        "instruction": "Say what the changelog says, in Patch's dry register, so the fix is sourced.",
    },
]

CLAIMS: list[dict[str, object]] = [
    {
        "text": "Driftpad says it turns a meeting into notes and action items while you are still saying goodbye",
        "source_url": HELP_URL,
        "source_quote": "Driftpad turns a meeting into notes and action items while you are still saying goodbye.",
        "in_test_log": False,
    },
    {
        "text": "the help page says under 30 seconds for a 30-minute meeting",
        "source_url": HELP_URL,
        "source_quote": "Transcripts are ready in under 30 seconds for a 30-minute meeting.",
        "in_test_log": False,
    },
    {
        "text": "version 2.4.1 fixes export to documents failing on large transcripts",
        "source_url": CHANGELOG_URL,
        "source_quote": "Version 2.4.1, released 2026-10-06: fixes export to documents failing on large transcripts.",
        "in_test_log": False,
    },
    {
        "text": "I gave Driftpad seven days",
        "source_url": None,
        "source_quote": None,
        "in_test_log": True,
    },
    {
        "text": "the transcript took 58 seconds for a 30-minute call",
        "source_url": None,
        "source_quote": None,
        "in_test_log": True,
    },
    {
        "text": "it found 11 of the 14 action items I had written down",
        "source_url": None,
        "source_quote": None,
        "in_test_log": True,
    },
    {
        "text": "it invented 2 action items that nobody said",
        "source_url": None,
        "source_quote": None,
        "in_test_log": True,
    },
    {
        "text": "on day 5 the export failed twice",
        "source_url": None,
        "source_quote": None,
        "in_test_log": True,
    },
    {
        "text": "version 2.4.1 arrived on day 6 and the export worked on day 6",
        "source_url": None,
        "source_quote": None,
        "in_test_log": True,
    },
    {
        "text": "speaker names were right in every call",
        "source_url": None,
        "source_quote": None,
        "in_test_log": True,
    },
]

ON_SCREEN = ["58 s vs under 30 s", "11 of 14 found, 2 invented", "Day 5: export failed twice"]
THUMB = "Slower than promised"


def fixtures() -> dict:
    """Mock outputs for every prompt the pipeline calls on this example. P2 call 1 and
    P3 are callables so the first pass is sent back and the second accepted."""
    from cap.demo import verify_from_source

    state = {"p2": 0, "p3": 0}

    def p2_call1(call):  # type: ignore[no-untyped-def]
        state["p2"] += 1
        first = state["p2"] == 1
        return ok(
            draft={
                "title": f"I gave {TOOL} seven days",
                "script": SCRIPT_V1 if first else SCRIPT,
                "shot_list": [
                    "the recorded call",
                    "the action-item view",
                    "the export dialog",
                    "the changelog",
                ],
                "caption": CAPTION,
                "on_screen_text": ON_SCREEN,
                "thumbnail_text": THUMB,
            },
            unsupported_requests=[],
        )

    def p3(call):  # type: ignore[no-untyped-def]
        state["p3"] += 1
        if state["p3"] == 1:
            return ok(verdict="revise", issues=CRITIC_ISSUES, estimated_length_s=600)
        return ok(verdict="accept", issues=[], estimated_length_s=600)

    return {
        ("P2", "1"): p2_call1,
        ("P2", "2"): ok(claims=CLAIMS, numeral_check=[]),
        ("P3", "default"): p3,
        ("P7", "default"): ok(
            variants=[
                {
                    "platform": "youtube_long",
                    "title": f"I gave {TOOL} seven days",
                    "caption": CAPTION,
                    "script_cut": "",
                    "duration_s": 600,
                    "on_screen_text": ON_SCREEN,
                    "thumbnail_text": THUMB,
                    "disclosure_preserved": True,
                },
                {
                    "platform": "youtube_short",
                    "title": f"{TOOL}: 58 seconds, not 30",
                    "caption": CAPTION,
                    "script_cut": (
                        "Task one: a 30-minute call. The transcript took 58 seconds in my test "
                        "log; the help page says under 30 seconds. Task two: it found 11 of the 14 "
                        "action items and invented 2. Verdict from the test log: Watch."
                    ),
                    "duration_s": 50,
                    "on_screen_text": ON_SCREEN[:2],
                    "thumbnail_text": "",
                    "disclosure_preserved": True,
                },
            ]
        ),
        ("P1", "A"): ok(
            verdict="pass",
            rule_findings=[
                {
                    "rule_id": "R-16",
                    "outcome": "not_triggered",
                    "evidence_span": "I gave Driftpad seven days",
                },
                {
                    "rule_id": "R-03",
                    "outcome": "not_triggered",
                    "evidence_span": "Slower than promised",
                },
            ],
        ),
        ("P1", "B"): ok(
            verdict="pass",
            rule_findings=[
                {"rule_id": "R-16", "outcome": "not_triggered", "evidence_span": "in my test log"},
            ],
        ),
        ("P9", "default"): ok(
            factors=[
                {"factor_id": "child_subject_matter", "outcome": "absent"},
                {"factor_id": "intended_audience_children", "outcome": "absent"},
                {"factor_id": "child_characters_or_toys", "outcome": "absent"},
                {"factor_id": "child_oriented_language", "outcome": "absent"},
                {"factor_id": "child_oriented_music_or_animation", "outcome": "absent"},
                {"factor_id": "child_celebrities_or_influencers", "outcome": "absent"},
            ],
            intended_audience_note="working adults who run meetings",
        ),
        ("P10", "A"): ok(
            claims=[{"text": c["text"], "source_url": c["source_url"]} for c in CLAIMS],
            added_claims=[],
            endorsement_wording=[],
        ),
        ("P10", "B"): verify_from_source,
        ("P10", "C"): verify_from_source,
    }


def numerals_covered() -> list[str]:
    """Every number in the script must appear in a claim; the tour's own check."""
    script_nums = set(re.findall(r"\d(?:[\d,.]*\d)?", SCRIPT))
    claim_nums = set(re.findall(r"\d(?:[\d,.]*\d)?", " ".join(str(c["text"]) for c in CLAIMS)))
    return sorted(script_nums - claim_nums)
