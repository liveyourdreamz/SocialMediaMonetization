"""Batch 1, gates: one home for the gate decision (M5) and the correctness defects the
22 Oct review verified (C5, C6, C7, H1-H3, G2, the mutation survivors). Every test
asserts on an outcome: a gate result, a post status, the claims checked, the card
questions, what enters the history."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from cap.agents import adapt
from cap.config import get_settings
from cap.domain.card import finalise, record_verdict
from cap.domain.verdict import gates_cleared
from cap.gates import (
    FileHistory,
    InMemoryHistory,
    Outcome,
    check_disclosure,
    check_ethics,
    check_facts,
    confirmed_sources,
    decide,
    default_history,
    record_approved,
)
from cap.gates.disclosure import MFK_FACTOR_WEIGHTS
from cap.gates.ethics import _BAIT
from cap.models import MockClient
from cap.pipeline import PipelineDeps, run_post
from cap.policy import AI_ROLE_DISCLOSURE
from cap.render import scenes
from cap.schema.post_record import (
    Channel,
    Claim,
    ClaimCheckResult,
    Decision,
    Platform,
    PostRecord,
    PostStatus,
    Variant,
    Verdict,
)
from tests.conftest import SOURCES, make_deps, ok, source_docs


def _factors(**outcomes: str) -> list[dict[str, str]]:
    """All six P9 factors, absent unless named."""
    return [{"factor_id": f, "outcome": outcomes.get(f, "absent")} for f in MFK_FACTOR_WEIGHTS]


def _block_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAP_FACT_MODE", "block")
    get_settings.cache_clear()


def _run(fixtures, record, tmp_path, **deps_kw) -> PostRecord:
    deps = make_deps(MockClient(fixtures), record, tmp_path)
    for k, v in deps_kw.items():
        setattr(deps, k, v)
    return run_post(record, SOURCES, deps)


def _approve(rec: PostRecord, acknowledged: bool = True) -> PostRecord:
    record_verdict(
        rec,
        reviewer="hemant",
        decision=Decision.APPROVE,
        opening_line="Here is the thing.",
        spot_check_supported=True,
        time_on_card_s=600,
    )
    return finalise(rec, reviewer="hemant", acknowledged=acknowledged)


def _passing() -> PostRecord:
    r = PostRecord(channel_id=Channel.A)
    g = r.gates
    g.originality.passed = True
    g.ethics.verdict = Verdict.PASS
    g.disclosure.passed, g.disclosure.mfk_band = True, "clear"
    g.facts.passed = True
    return r


# ---- M5: one rule, three outcomes ----------------------------------------------------


def test_the_verdict_is_pass_questions_or_reject():
    r = _passing()
    assert decide(r).outcome == Outcome.PASS and gates_cleared(r)

    r.gates.ethics.verdict = Verdict.ESCALATE
    v = decide(r)
    assert v.outcome == Outcome.QUESTIONS and v.questions[0].startswith("Ethics")
    assert not gates_cleared(r)  # a question nobody settled
    r.approval.decisions_acknowledged = list(v.questions)
    assert gates_cleared(r)

    for breaks in (
        lambda g: setattr(g.originality, "passed", False),
        lambda g: setattr(g.ethics, "verdict", Verdict.FAIL),
        lambda g: setattr(g.disclosure, "ad_prefix_ok", False),
        lambda g: setattr(g.disclosure, "mfk_band", "likely"),
        lambda g: setattr(g.facts, "passed", False),
    ):
        bad = _passing()
        breaks(bad.gates)
        bad.approval.decisions_acknowledged = ["anything at all"]
        assert decide(bad).outcome == Outcome.REJECT and not gates_cleared(bad)


def test_a_pipeline_reject_names_every_failed_gate(fixtures, record, tmp_path):
    fixtures[("P1", "A")] = ok(
        verdict="fail",
        rule_findings=[{"rule_id": "R-01", "outcome": "block", "evidence_span": "them"}],
    )
    fixtures[("P9", "default")] = ok(factors=_factors(child_subject_matter="present"))
    fixtures[("P9", "default")]["factors"][1]["outcome"] = "present"
    out = _run(fixtures, record, tmp_path)
    assert out.status == PostStatus.REJECTED
    assert set(decide(out).failed_gates) == {"ethics", "disclosure"}
    assert not out.flags.needs_decision  # a failure is never a question


# ---- G3: the published source list ---------------------------------------------------


def test_confirmed_sources_never_list_the_test_log_or_an_unconfirmed_link():
    r = PostRecord(channel_id=Channel.A)
    r.draft.claims = [
        Claim(text="a", source_url="https://ok.example/a", status="supported"),
        Claim(text="b", source_url="testlog://post-1", status="supported", in_test_log=True),
        Claim(text="c", source_url="https://no.example/c", status="unsupported"),
    ]
    assert confirmed_sources(r) == ["https://ok.example/a"]


def test_the_published_source_list_and_package_leave_out_the_test_log(client, record, tmp_path):
    from cap.ui.views import package

    out = run_post(record, SOURCES, make_deps(client, record, tmp_path))
    assert any(
        c.status == "supported" and (c.source_url or "").startswith("testlog://")
        for c in out.draft.claims
    )  # the happy path does confirm claims against the test log
    assert out.draft.source_list == [SOURCES[0]["url"]]
    assert package(out)["base"]["sources"] == [SOURCES[0]["url"]]


# ---- C5: discovery never drops a claim -----------------------------------------------


def _drafted(record: PostRecord) -> PostRecord:
    record.draft.title = "Notely"
    record.draft.script = "Notely syncs in 12 seconds. The app has 40 themes."
    record.draft.claims = [
        Claim(text="Notely syncs in 12 seconds"),
        Claim(text="The app has 40 themes"),  # as code adds for an unlisted numeral
    ]
    return record


def test_an_empty_discovery_list_still_checks_every_drafter_claim(record, monkeypatch):
    _block_mode(monkeypatch)
    fx = {
        ("P10", "A"): ok(claims=[], added_claims=[], endorsement_wording=[]),
        ("P10", "B"): ok(claim_status="unsupported", supporting_quote=""),
    }
    gate, _ = check_facts(_drafted(record), MockClient(fx), source_docs())
    assert gate.checked == 2 and not gate.passed
    assert {cc.claim_text for cc in gate.claim_checks} == {
        "Notely syncs in 12 seconds",
        "The app has 40 themes",
    }


def test_discovery_adds_and_rewords_but_dedupes_on_normalised_text(record):
    fx = {
        ("P10", "A"): ok(
            claims=[{"text": "  notely syncs in  12 seconds. ", "source_url": None}],
            added_claims=["Sync failed on day 3"],
            endorsement_wording=[],
        ),
        ("P10", "B"): ok(claim_status="unsupported", supporting_quote=""),
    }
    gate, _ = check_facts(_drafted(record), MockClient(fx), source_docs())
    assert sorted(cc.claim_text for cc in gate.claim_checks) == [
        "Notely syncs in 12 seconds",
        "Sync failed on day 3",
        "The app has 40 themes",
    ]


def test_in_the_pipeline_an_empty_discovery_checks_the_drafted_claims(fixtures, record, tmp_path):
    fixtures[("P10", "A")] = ok(claims=[], added_claims=[], endorsement_wording=[])
    out = _run(fixtures, record, tmp_path)
    assert out.gates.facts.checked == len(fixtures[("P2", "2")]["claims"]) > 0


def test_arabic_indic_digits_name_the_same_claim():
    from cap.gates.facts import claim_key

    assert claim_key("نحو ٣٠ في المئة من الغيوم تمطر.") == claim_key(
        "نحو 30 في المئة من الغيوم تمطر"
    )


# ---- C6 / E3: a judge's outcome is the worse of what it said and what it found ------


def test_a_judge_fail_without_findings_escalates_to_the_card(fixtures, record, tmp_path):
    fixtures[("P1", "A")] = ok(
        verdict="fail",
        rule_findings=[{"rule_id": "R-09", "outcome": "not_triggered", "evidence_span": ""}],
        explanation="health claim without a source",
    )
    out = _run(fixtures, record, tmp_path)
    assert out.gates.ethics.verdict == Verdict.ESCALATE
    assert out.status == PostStatus.AWAITING_APPROVAL
    assert any("health claim without a source" in q for q in out.flags.needs_decision)


def test_a_judge_pass_with_a_blocking_finding_fails_the_post(fixtures, record, tmp_path):
    fixtures[("P1", "B")] = ok(
        verdict="pass",
        rule_findings=[{"rule_id": "R-01", "outcome": "block", "evidence_span": "those people"}],
    )
    out = _run(fixtures, record, tmp_path)
    assert out.gates.ethics.verdict == Verdict.FAIL and out.status == PostStatus.REJECTED


def test_a_judge_escalate_with_a_blocking_finding_fails(fixtures, record, tmp_path):
    fixtures[("P1", "A")] = ok(
        verdict="escalate",
        rule_findings=[{"rule_id": "R-01", "outcome": "block", "evidence_span": "x"}],
    )
    assert _run(fixtures, record, tmp_path).gates.ethics.verdict == Verdict.FAIL


# ---- H1 / E5: R-05 bait patterns -----------------------------------------------------

ORDINARY = [
    "Each comment section is its own small database.",
    "The comment section loads lazily.",
    "Tools like the video editor export to MP4.",
    "Like the video editor, the audio tool autosaves.",
    "Share this post with your team.",
    "Share this video file with the editor.",
    "Comment API calls are rate limited.",
    "Comment on every pull request.",
    "Comment out the line, then type ls in the terminal.",
    "Then type YES at the prompt to confirm the install.",
    "Type no more than 40 characters.",
    "React for beginners covers hooks.",
    "We tag three people on every release.",
    "If you like this approach, the docs explain more.",
    "Leave a comment below the function to explain it.",
    "You can like the video and the app records it.",
    "Like the post office, the queue is first in, first out.",
    "The share sheet lets you share if your admin allows it.",
    "Tools like this are common in 2026.",
    "We vote with our wallets, and the results are in.",
    "Subscribe for more.",
]
BAIT = [
    "Comment YES if you agree and tag 3 friends who need this!",
    "comment below if you've tried it",
    "👉 Comment BREAD below",
    'Comment "STALE" for part two.',
    "Like if you agree!",
    "So, like this video if you want more.",
    "Like this video and subscribe.",
    "Share if you agree.",
    "Share this video if you learned something.",
    "Type YES in the comments.",
    "Tag three friends who should see this.",
    "React with fire if you want part two.",
    "React for the next part.",
    "Vote yes in the comments.",
    "Smash that like button.",
]


@pytest.mark.parametrize("text", ORDINARY)
def test_ordinary_software_prose_is_not_bait(text):
    assert _BAIT.search(text) is None, _BAIT.search(text)


@pytest.mark.parametrize("text", BAIT)
def test_real_bait_is_caught(text):
    assert _BAIT.search(text)


def test_a_script_about_the_comment_section_reaches_the_judges(record, client):
    record.draft.script = "Each comment section is its own small database. Like the video editor."
    record.variants = [
        Variant(platform=Platform.YOUTUBE_LONG, title="t", caption=AI_ROLE_DISCLOSURE)
    ]
    gate = check_ethics(record, client)
    assert gate.verdict == Verdict.PASS and not gate.hard_rule_findings
    assert [c for c in client.calls if c.spec.prompt_id == "P1"]


def test_bait_in_the_long_form_script_hard_fails(record, client):
    record.draft.script = "Starch recrystallises. Comment YES if you agree."
    record.variants = [
        Variant(platform=Platform.YOUTUBE_LONG, title="t", caption=AI_ROLE_DISCLOSURE)
    ]
    gate = check_ethics(record, client)
    assert gate.verdict == Verdict.FAIL and gate.rule_ids == ["R-05"]
    assert not [c for c in client.calls if c.spec.prompt_id == "P1"]


# ---- H2 / A3: the adapter ------------------------------------------------------------


def test_a_variant_without_a_caption_keeps_the_draft_caption(fixtures, record, tmp_path):
    fixtures[("P7", "default")] = ok(
        variants=[{"platform": "youtube_long", "title": "We tested Notely for 7 days"}]
    )
    out = _run(fixtures, record, tmp_path)
    assert out.variants[0].caption == out.draft.caption
    assert "R-07" not in out.gates.ethics.rule_ids
    assert out.status == PostStatus.AWAITING_APPROVAL


def test_a_variant_that_introduces_a_number_is_dropped(record):
    record.draft.title, record.draft.caption = "Notely", AI_ROLE_DISCLOSURE
    record.draft.script = "We tested Notely for 7 days."
    fx = {
        ("P7", "default"): ok(
            variants=[
                {"platform": "youtube_long", "title": "Notely", "caption": AI_ROLE_DISCLOSURE},
                {
                    "platform": "youtube_short",
                    "title": "Notely: 99% of users quit",
                    "caption": AI_ROLE_DISCLOSURE,
                    "script_cut": "We tested Notely for 7 days.",
                },
            ]
        )
    }
    out = adapt(record, MockClient(fx), [Platform.YOUTUBE_LONG, Platform.YOUTUBE_SHORT])
    assert [v.platform for v in out.variants] == [Platform.YOUTUBE_LONG]
    assert not any("99" in v.title for v in out.variants)


# ---- H3 / G2 / D4 / D5: disclosure and made for kids ---------------------------------


@pytest.mark.parametrize(
    "factors",
    [[], [{"factor_id": "child_subject_matter", "outcome": "absent"}], _factors() * 2],
    ids=["none", "one", "duplicated"],
)
def test_an_incomplete_factor_list_is_unscored_not_clear(record, factors):
    gate = check_disclosure(record, MockClient({("P9", "default"): ok(factors=factors)}))
    assert gate.mfk_band == "unscored" and not gate.passed


def test_an_unscored_result_is_a_card_question_and_publishable_once_settled(
    fixtures, record, tmp_path
):
    fixtures[("P9", "default")] = ok(status="blocked", blocked_reason="cannot assess")
    out = _run(fixtures, record, tmp_path)
    assert out.status == PostStatus.AWAITING_APPROVAL, out.flags.stop_reason
    assert [q for q in out.flags.needs_decision if q.startswith("Made for kids: unscored")]
    assert not gates_cleared(out)
    done = _approve(out)
    assert done.status == PostStatus.APPROVED and gates_cleared(done)


def test_zero_factors_in_the_pipeline_is_a_question_too(fixtures, record, tmp_path):
    fixtures[("P9", "default")] = ok(factors=[])
    out = _run(fixtures, record, tmp_path)
    assert out.gates.disclosure.mfk_band == "unscored"
    assert out.status == PostStatus.AWAITING_APPROVAL
    assert any(q.startswith("Made for kids: unscored") for q in out.flags.needs_decision)


def test_doubtful_never_counts_as_passed(record):
    fx = {("P9", "default"): ok(factors=_factors(child_subject_matter="unclear"))}
    gate = check_disclosure(record, MockClient(fx))
    assert gate.mfk_band == "doubtful" and not gate.passed


def test_an_affiliate_marker_makes_the_post_paid(fixtures, record):
    record.draft.caption = "Try it: https://shop.example/notely?tag=patch-21 " + AI_ROLE_DISCLOSURE
    record.variants = [
        Variant(platform=Platform.YOUTUBE_LONG, title="We tested", caption=record.draft.caption)
    ]
    gate = check_disclosure(record, MockClient(fixtures))
    assert gate.paid_promo and not gate.ad_prefix_ok and not gate.passed
    record.gates.disclosure = gate
    assert "disclosure" in decide(record).failed_gates


# ---- D41 / F3 / F5: facts outcomes ---------------------------------------------------


def test_unconfirmed_claims_are_one_card_question_in_flag_mode(fixtures, record, tmp_path):
    fixtures[("P10", "B")] = ok(claim_status="unsupported", supporting_quote="")
    out = _run(fixtures, record, tmp_path)
    n = out.gates.facts.checked
    facts_qs = [q for q in out.flags.needs_decision if q.startswith("Facts:")]
    assert out.status == PostStatus.AWAITING_APPROVAL and len(facts_qs) == 1
    assert f"{n} claims could not be confirmed" in facts_qs[0]
    assert not gates_cleared(out) and gates_cleared(_approve(out))


def test_unconfirmed_claims_reject_in_block_mode(fixtures, record, tmp_path, monkeypatch):
    _block_mode(monkeypatch)
    fixtures[("P10", "B")] = ok(claim_status="unsupported", supporting_quote="")
    out = _run(fixtures, record, tmp_path)
    assert out.status == PostStatus.REJECTED and "facts" in decide(out).failed_gates


def test_an_outdated_source_is_reported_as_outdated(record, monkeypatch):
    _block_mode(monkeypatch)
    record.draft.claims = [Claim(text="Sync completes within 10 seconds")]
    fx = {
        ("P10", "A"): ok(claims=[], added_claims=[], endorsement_wording=[]),
        ("P10", "B"): ok(claim_status="outdated", supporting_quote=""),
    }
    gate, _ = check_facts(record, MockClient(fx), source_docs())
    assert [cc.final for cc in gate.claim_checks] == ["outdated"]
    assert record.draft.claims[0].status == "outdated" and not gate.passed


def test_endorsement_wording_rejects_in_flag_mode(fixtures, record, tmp_path):
    assert get_settings().fact_mode == "flag"
    a = dict(fixtures[("P10", "A")])
    a["endorsement_wording"] = ["the best note app you can buy"]
    fixtures[("P10", "A")] = a
    out = _run(fixtures, record, tmp_path)
    assert not out.gates.facts.passed and out.status == PostStatus.REJECTED


def test_the_question_lists_each_unconfirmed_claim():
    r = _passing()
    r.gates.facts.claim_checks = [
        ClaimCheckResult(claim_text="one", final="supported"),
        ClaimCheckResult(claim_text="two", final="unsupported"),
    ]
    assert decide(r).questions == ("Facts: 1 claim could not be confirmed: two",)


# ---- C7: the post history ------------------------------------------------------------


def _approved_post(script: str, **flags: bool) -> PostRecord:
    r = PostRecord(channel_id=Channel.A, status=PostStatus.APPROVED)
    r.draft.script = script
    r.approval.decided_at = datetime.now(UTC)
    for k, v in flags.items():
        setattr(r.flags, k, v)
    return r


@pytest.fixture
def real_backend(monkeypatch):
    """An approval drafted by the real model; mock drafts never enter the history."""
    from cap.config import get_settings

    monkeypatch.setenv("CAP_MODEL_BACKEND", "anthropic")
    monkeypatch.setenv("CAP_ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_only_approved_real_posts_enter_the_history(tmp_path, real_backend):
    h = FileHistory(tmp_path / "history.jsonl")
    real = _approved_post("real")
    assert record_approved(real, h)
    assert not record_approved(_approved_post("canary", canary=True), h)  # D23
    assert not record_approved(_approved_post("tour", tour=True), h)
    waiting = _approved_post("waiting")
    waiting.status = PostStatus.AWAITING_APPROVAL
    assert not record_approved(waiting, h)
    assert [s for _, s in h.recent("A", 90)] == ["real"]
    assert record_approved(real, h)  # idempotent per post id
    assert len((tmp_path / "history.jsonl").read_text().splitlines()) == 1


def test_the_file_history_keeps_a_90_day_window_and_survives_a_bad_line(tmp_path):
    path = tmp_path / "history.jsonl"
    old = _approved_post("old")
    old.approval.decided_at = datetime.now(UTC) - timedelta(days=120)
    h = FileHistory(path)
    h.add(old)
    with path.open("a", encoding="utf-8") as f:
        f.write("not json\n")
    h.add(_approved_post("new"))
    lines = [json.loads(ln) for ln in path.read_text().splitlines() if ln.startswith("{")]
    assert {ln["channel"] for ln in lines} == {"A"}
    assert [s for _, s in FileHistory(path).recent("B", 90)] == ["new"]  # all channels


def test_the_pipeline_reads_the_history_under_the_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CAP_DATA_DIR", str(tmp_path / "d"))
    get_settings.cache_clear()
    deps = PipelineDeps(client=MockClient({}))
    assert isinstance(deps.history, FileHistory)
    assert deps.history.path == default_history().path == tmp_path / "d" / "history.jsonl"


def test_an_approved_post_makes_its_duplicate_fail_originality(
    fixtures, record, tmp_path, monkeypatch, real_backend
):
    monkeypatch.setenv("CAP_DATA_DIR", str(tmp_path / "d"))
    get_settings.cache_clear()
    first = _run(fixtures, record, tmp_path / "r1", history=default_history())
    assert first.status == PostStatus.AWAITING_APPROVAL
    approved = _approve(first)
    assert record_approved(approved)
    again = PostRecord(channel_id=Channel.A, brief=record.brief)
    out = _run(fixtures, again, tmp_path / "r2", history=default_history())
    assert out.status == PostStatus.REJECTED
    assert out.gates.originality.nearest_post_id == approved.post_id


def test_a_canary_in_memory_never_shapes_originality():
    h = InMemoryHistory()
    h.add(_approved_post("same words here", canary=True))
    assert h.recent("A", 90) == []


# ---- render: numbers with thousands separators ---------------------------------------


def test_scene_planning_reads_numbers_with_thousands_separators():
    plan = scenes.plan(
        [
            "The archive ran a 1,000-day test on the format.",
            "On day 1,200 the app crashed.",
            "It answered 1,200 of 1,500 questions and invented 1,000 more.",
        ],
        character="Patch",
        title="Why film fades",
        topic="film",
        on_screen=[],
        media=[],
    )
    assert plan[0].days == 1000
    assert plan[1].day == 1200
    assert (plan[2].number, plan[2].total, plan[2].extra) == (1200.0, 1500, 1000.0)


# ---- research: Arabic topics and URL matching ----------------------------------------


def test_an_arabic_topic_searches_arabic_wikipedia(monkeypatch):
    from cap import research

    seen: list[str] = []

    class _Client:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None):
            seen.append(url)
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"query": {"search": [{"title": "مطر"}]}},
            )

    monkeypatch.setattr(research.httpx, "Client", _Client)
    monkeypatch.setattr("cap.fetch.fetch_sources", lambda urls, timeout=20.0: [])
    research.find_sources("لماذا تمطر الغيوم", "C")
    assert seen == ["https://ar.wikipedia.org/w/api.php"]
    assert research.wikipedia_language("How clouds form", "B") == "en"


@pytest.mark.parametrize(
    "written",
    [
        "[the page](https://en.wikipedia.org/wiki/Caf%C3%A9).",
        "<https://en.wikipedia.org/wiki/Café/>",
        "**https://en.wikipedia.org/wiki/Café**,",
        "https://EN.wikipedia.org/wiki/Café#History |",
    ],
)
def test_the_scouts_chosen_url_matches_its_search_result(written):
    from cap.research import url_key

    searched = "https://en.wikipedia.org/wiki/Caf%C3%A9"
    found = [m for m in __import__("re").findall(r"https?://[^\s<>\"']+", written)]
    assert url_key(found[0]) == url_key(searched)
