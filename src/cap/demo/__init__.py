"""A fake Patch episode and the mock model responses that let the whole pipeline run
without a key. Used by `cap demo` and by the test suite (the happy path, S1). The
fixtures are deliberately clean; tests override individual responses for failures."""

from __future__ import annotations

import re

from cap.models import SourceDoc
from cap.policy import AI_ROLE_DISCLOSURE

SCRIPT = (
    "[[HEMANT_OPENING_LINE]] Today we test Notely, a note-taking tool, for 7 days. "
    "On day 3 the sync failed twice in our test log. Notely stores notes locally first, "
    "then syncs; the vendor says sync completes within 10 seconds on a normal connection. "
    "Our test measured 12 seconds on average. Verdict from the test log: it works, with "
    "one caveat."
)

CAPTION = f"Sources in the description. {AI_ROLE_DISCLOSURE}"

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

TEST_LOG = (
    "Test log, Notely, 7 days. Day 3: sync failed twice. "
    "Sync time measured 12 seconds on average over 40 runs."
)

CHARACTER_BLOCK = (
    '<character_block version="1.0">Patch: AI tools tester. Narrates logged tests only; '
    "never claims a body, a family, a job or own money. Anchor element: the test log. "
    "Catchphrase cap 1.</character_block>"
)


def source_docs() -> list[SourceDoc]:
    return [SourceDoc(**s) for s in SOURCES]


def ok(**fields):  # type: ignore[no-untyped-def]
    return {
        "status": "ok",
        "blocked_reason": None,
        "injection_suspected": False,
        "explanation": "fixture",
        **fields,
    }


# What the mock canary writer plants, per fault type: (where, the planted text). The
# real writer (P14) plants subtler faults; these exist so the loop runs offline.
MOCK_FAULTS = {
    "unsourced_number": ("script", "About 73% of people never notice this."),
    "missing_ad_prefix": ("caption", "Thanks to today's sponsor for making this episode possible."),
    "out_group_title": ("title", "Why THEY keep getting this wrong: "),
    "first_person_human_claim": ("script", "When I was a boy, my father showed me this."),
    "sponsor_verdict": ("script", "Our sponsor's version is simply the best one to buy."),
    "kids_styled_visual": ("on_screen_text", "Bright cartoon balloons and a sing-along jingle"),
    "reused_cold_open": ("script", "From orbit, here's what I see. A white line of salt."),
}


def mock_canary(call):  # type: ignore[no-untyped-def]
    """The offline P14: plants the named fault in the base draft, one fault only."""
    import json as _json
    import re as _re

    fault = _re.search(r"<fault_type>(\w+)</fault_type>", call.user).group(1)
    base = _json.loads(_re.search(r"<base_draft>(.*)</base_draft>", call.user, _re.S).group(1))
    where, text = MOCK_FAULTS[fault]
    draft = {
        k: base.get(k, "" if k not in ("shot_list", "on_screen_text") else [])
        for k in ("title", "script", "shot_list", "caption", "on_screen_text", "thumbnail_text")
    }
    if where == "title":
        draft["title"] = text + draft["title"]
    elif where == "on_screen_text":
        draft["on_screen_text"] = [*draft["on_screen_text"], text]
    elif where == "caption":
        draft["caption"] = text
    else:
        draft["script"] = draft["script"] + " " + text
    return ok(
        canary_draft=draft,
        planted_fault={
            "type": fault,
            "span": text,
            "why_catchable": "planted by the offline canary writer",
        },
    )


def happy_fixtures() -> dict:
    web = str(SOURCES[0]["url"])
    claims: list[dict[str, object]] = [
        {
            "text": "Notely stores notes locally first, then syncs",
            "source_url": web,
            "source_quote": "Notes are stored locally first and then synced.",
            "in_test_log": False,
        },
        {
            "text": "the vendor says sync completes within 10 seconds on a normal connection",
            "source_url": web,
            "source_quote": "Sync completes within 10 seconds on a normal connection.",
            "in_test_log": False,
        },
        {
            "text": "we tested Notely for 7 days",
            "source_url": None,
            "source_quote": None,
            "in_test_log": True,
        },
        {
            "text": "on day 3 the sync failed twice",
            "source_url": None,
            "source_quote": None,
            "in_test_log": True,
        },
        {
            "text": "our test measured 12 seconds on average",
            "source_url": None,
            "source_quote": None,
            "in_test_log": True,
        },
    ]

    return {
        ("P2", "1"): ok(
            draft={
                "title": "We tested Notely for 7 days",
                "script": SCRIPT,
                "shot_list": ["desk", "app"],
                "caption": CAPTION,
                "on_screen_text": ["Day 3: sync failed twice"],
                "thumbnail_text": "7-day test",
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
                    "caption": CAPTION,
                    "script_cut": "",
                    "duration_s": 540,
                    "on_screen_text": ["Day 3: sync failed twice"],
                    "thumbnail_text": "7-day test",
                    "disclosure_preserved": True,
                },
                {
                    "platform": "youtube_short",
                    "title": "Notely sync: 12 seconds, not 10",
                    "caption": AI_ROLE_DISCLOSURE,
                    "script_cut": "On day 3 the sync failed twice; our test measured 12 seconds on average.",
                    "duration_s": 45,
                    "on_screen_text": [],
                    "thumbnail_text": "",
                    "disclosure_preserved": True,
                },
            ]
        ),
        ("P14", "default"): mock_canary,
        ("P1", "A"): ok(verdict="pass", rule_findings=[]),
        ("P1", "B"): ok(verdict="pass", rule_findings=[]),
        ("P9", "default"): ok(
            factors=[
                {"factor_id": "child_subject_matter", "outcome": "absent"},
                {"factor_id": "intended_audience_children", "outcome": "absent"},
                {"factor_id": "child_characters_or_toys", "outcome": "absent"},
                {"factor_id": "child_oriented_language", "outcome": "absent"},
                {"factor_id": "child_oriented_music_or_animation", "outcome": "absent"},
                {"factor_id": "child_celebrities_or_influencers", "outcome": "absent"},
            ],
            intended_audience_note="adults evaluating software",
        ),
        ("P10", "A"): ok(
            claims=[{"text": c["text"], "source_url": c["source_url"]} for c in claims],  # type: ignore[index]
            added_claims=[],
            endorsement_wording=[],
        ),
        ("P10", "B"): verify_from_source,
        ("P10", "C"): verify_from_source,
    }


def verify_from_source(call) -> dict:  # type: ignore[no-untyped-def]
    """P10 B and C in mock mode: quote the source sentence that shares most words with
    the claim, so the exact-substring check in code is exercised for real."""
    tail = call.user_tail
    body = call.sources[0].body if call.sources else ""
    claim = tail.split("<claim>")[1].split("</claim>")[0].lower() if "<claim>" in tail else ""
    key_words = [w for w in re.findall(r"[a-z0-9]+", claim) if len(w) > 2]
    best, best_hits = "", 0
    for sent in re.split(r"(?<=\.)\s+", body.replace("\n", " ")):
        s = sent.strip()
        hits = sum(1 for w in key_words if w in s.lower())
        if hits > best_hits:
            best, best_hits = s, hits
    if key_words and best_hits >= max(2, (len(key_words) + 1) // 2):
        return ok(claim_status="supported", supporting_quote=best, note="")
    return ok(claim_status="unsupported", supporting_quote="", note="no matching sentence")
