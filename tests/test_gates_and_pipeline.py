from datetime import UTC, datetime, timedelta

import pytest

from cap.agents import AgentBlockedError, draft
from cap.demo import CHARACTER_BLOCK, TEST_LOG
from cap.gates import (
    InMemoryHistory,
    check_disclosure,
    check_ethics,
    check_facts,
    check_originality,
)
from cap.gates.disclosure import mfk_band, mfk_score
from cap.gates.ethics import _BAIT, _judge_user_message, aggregate, verdict_from_findings
from cap.gates.facts import quote_in_source
from cap.models import Completion, MockClient
from cap.pipeline import run_post
from cap.policy import AI_ROLE_DISCLOSURE
from cap.schema.post_record import (
    Channel,
    Claim,
    JudgeResult,
    Platform,
    PostRecord,
    PostStatus,
    RuleFinding,
    RuleOutcome,
    Sponsor,
    Variant,
    Verdict,
)
from tests.conftest import SOURCES, make_deps, ok, source_docs


def test_happy_path_reaches_queue(client, record, tmp_path):
    out = run_post(record, SOURCES, make_deps(client, record, tmp_path))
    assert out.status == PostStatus.AWAITING_APPROVAL, out.history
    assert out.gates.all_passed, out.gates.model_dump()
    assert out.flags.card_prepared and out.approval.spot_check is not None
    assert out.asset.sha256 and (tmp_path / f"{out.post_id}.render.txt").exists()
    assert len(out.variants) == 2
    assert all(c.status == "supported" for c in out.draft.claims), out.gates.facts.model_dump()
    # judges: one call per variant per judge (D26), isolated: the user turn is exactly
    # the judge message (content, manifest, sponsor) and nothing else
    p1 = [c for c in client.calls if c.spec.prompt_id == "P1"]
    assert len(p1) == 4
    for c in p1:
        variant = next(v for v in out.variants if v.platform.value in c.user)
        assert c.user == _judge_user_message(out, variant)
        assert c.user_tail == "" and c.sources == []
        assert "<rulebook version" in c.system[-1]["text"]
        assert "critic" not in c.user.lower() and "judge" not in c.user.lower()
    # sources only ever reach a model as tool results inside this call's nonce tags
    for c in client.calls:
        assert SOURCES[0]["body"] not in c.user and SOURCES[0]["body"] not in c.user_tail
        for block in c.tool_result_blocks():
            assert f'<untrusted_text id="{c.nonce}">' in block["text"]
        assert c.nonce in c.system[0]["text"]


def test_judge_disagreement_escalates_and_parks(fixtures, record, tmp_path):
    fixtures[("P1", "B")] = ok(
        verdict="escalate",
        rule_findings=[{"rule_id": "R-16", "outcome": "escalate", "evidence_span": "our test"}],
    )
    client = MockClient(fixtures)
    out = run_post(record, SOURCES, make_deps(client, record, tmp_path))
    assert out.status == PostStatus.PARKED
    assert out.gates.ethics.verdict == Verdict.ESCALATE and out.gates.ethics.disagreement


def test_hard_rule_fires_before_judges_and_stops_the_gate(record, client):
    record.draft.script = "x"
    record.variants = [
        Variant(platform=Platform.YOUTUBE_LONG, title="t", caption="Comment YES if you agree"),
        Variant(platform=Platform.YOUTUBE_SHORT, title="t", caption=AI_ROLE_DISCLOSURE),
    ]
    gate = check_ethics(record, client)
    assert gate.verdict == Verdict.FAIL
    assert {"R-05", "R-07"} <= set(gate.rule_ids)
    assert not [c for c in client.calls if c.spec.prompt_id == "P1"]  # no judge call at all


@pytest.mark.parametrize(
    "text,fires",
    [
        ("Tools like this are common in 2026.", False),
        ("A chart like this one shows the trend; share your results in the comments.", False),
        ("We vote with our wallets, and the results are in.", False),
        ("Comment YES if you agree and tag 3 friends who need this!", True),
        ("Tag three friends who should see this.", True),
        ("React with fire if you want part two.", True),
        ("React for the next part.", True),
        ("Smash that like button.", True),
        ("Share this video if you learned something.", True),
        ("Subscribe for more.", False),
    ],
)
def test_r05_regex_on_prose(text, fires):
    assert bool(_BAIT.search(text)) is fires, text


def test_r07_is_checked_per_variant(record, client):
    record.draft.script = "x"
    record.draft.caption = AI_ROLE_DISCLOSURE
    record.variants = [
        Variant(platform=Platform.YOUTUBE_LONG, title="t", caption=AI_ROLE_DISCLOSURE),
        Variant(platform=Platform.YOUTUBE_SHORT, title="t", caption="no disclosure here"),
    ]
    gate = check_ethics(record, client)
    assert gate.verdict == Verdict.FAIL
    assert any("youtube_short caption" in f.evidence_span for f in gate.hard_rule_findings)


def test_verdict_follows_findings_not_model_claim():
    assert (
        verdict_from_findings([RuleFinding(rule_id="R-01", outcome=RuleOutcome.BLOCK)])
        == Verdict.FAIL
    )
    assert (
        verdict_from_findings([RuleFinding(rule_id="R-02", outcome=RuleOutcome.REVISE)])
        == Verdict.FAIL
    )
    assert (
        verdict_from_findings([RuleFinding(rule_id="R-02", outcome=RuleOutcome.ESCALATE)])
        == Verdict.ESCALATE
    )
    assert verdict_from_findings([]) == Verdict.PASS
    a = JudgeResult(judge="A", model="m", verdict=Verdict.PASS)
    b = JudgeResult(judge="B", model="m", verdict=Verdict.PASS, status="timeout")
    assert aggregate([a, b])[0] == Verdict.ESCALATE  # no judge ever passes alone


def test_refusal_is_not_retried_and_escalates(fixtures, record):
    calls = {"n": 0}

    class Refusing(MockClient):
        def _complete(self, call):
            if call.spec.prompt_id == "P1":
                calls["n"] += 1
                return Completion(raw="", stop_reason="refusal", stop_category="policy")
            return super()._complete(call)

    record.draft.caption = AI_ROLE_DISCLOSURE
    gate = check_ethics(record, Refusing(fixtures))
    assert gate.verdict == Verdict.ESCALATE
    assert calls["n"] == 2  # one per judge, no retry on refusal
    assert all(j.status == "refused" for j in gate.judges)
    assert all(j.explanation == "model_refusal:policy" for j in gate.judges)


def test_malformed_output_retries_once_then_blocks(fixtures, record):
    seen = {"n": 0}

    class Broken(MockClient):
        def _complete(self, call):
            if call.spec.prompt_id == "P9":
                seen["n"] += 1
                return Completion(raw="not json", stop_reason="end_turn")
            return super()._complete(call)

    gate = check_disclosure(record, Broken(fixtures))
    assert seen["n"] == 2 and gate.passed is False and gate.mfk_band == "unscored"


# ---- facts --------------------------------------------------------------------------


def _one_claim(fixtures, record, text, url):
    record.draft.claims = [Claim(text=text, source_url=url)]
    fixtures[("P10", "A")] = ok(
        claims=[{"text": text, "source_url": url}], added_claims=[], endorsement_wording=[]
    )


def test_fact_quote_must_be_exact_substring(fixtures, record):
    fixtures[("P10", "B")] = ok(
        claim_status="supported", supporting_quote="Sync completes within ten seconds", note=""
    )
    _one_claim(fixtures, record, "sync completes within 10 seconds", SOURCES[0]["url"])
    gate, _ = check_facts(record, MockClient(fixtures), source_docs())
    assert not gate.passed and gate.claim_checks[0].final == "unsupported"
    assert gate.claim_checks[0].quote_in_source is False


def test_quote_check_accepts_json_escapes_and_curly_quotes():
    body = 'The vendor said "sync is\nfast" and moved on.'
    assert quote_in_source(
        'The vendor said \\"sync is\\nfast\\"', body
    )  # copied from the JSON view
    assert quote_in_source("The vendor said “sync is fast”", body)
    assert not quote_in_source("sync is slow", body)
    assert not quote_in_source("", body)


def test_second_verifier_disagreement_blocks(fixtures, record):
    fixtures[("P10", "C")] = ok(claim_status="unsupported", supporting_quote="", note="stretch")
    _one_claim(fixtures, record, "Notely stores notes locally first, then syncs", SOURCES[0]["url"])
    gate, _ = check_facts(record, MockClient(fixtures), source_docs())
    assert not gate.passed and gate.claim_checks[0].disagreement
    assert "second verifier disagreed" in gate.claim_checks[0].note


def test_cited_url_not_among_sources_is_missing_source(fixtures, record):
    _one_claim(
        fixtures,
        record,
        "Notely stores notes locally first, then syncs",
        "https://elsewhere.example/",
    )
    client = MockClient(fixtures)
    gate, _ = check_facts(record, client, source_docs())
    assert gate.claim_checks[0].final == "missing_source"
    assert not [c for c in client.calls if c.spec.prompt_id == "P10" and c.spec.call in ("B", "C")]


def test_null_source_claim_tries_every_source_and_keeps_drafter_fields(fixtures, record):
    record.draft.claims = [
        Claim(
            text="Notely stores notes locally first, then syncs", source_url=None, in_test_log=False
        ),
    ]
    fixtures[("P10", "A")] = ok(
        claims=[{"text": record.draft.claims[0].text, "source_url": None}],
        added_claims=["our test measured 12 seconds on average"],
        endorsement_wording=[],
    )
    client = MockClient(fixtures)
    gate, _ = check_facts(record, client, source_docs(), test_log=TEST_LOG)
    by_text = {cc.claim_text: cc for cc in gate.claim_checks}
    assert by_text["Notely stores notes locally first, then syncs"].final == "supported"
    assert (
        by_text["our test measured 12 seconds on average"].final == "supported"
    )  # added claim, test log source
    assert by_text["our test measured 12 seconds on average"].source_url.startswith("testlog://")
    assert gate.passed


def test_blocked_discovery_parks(fixtures, record, tmp_path):
    fixtures[("P10", "A")] = {"status": "blocked", "blocked_reason": "script missing"}
    client = MockClient(fixtures)
    out = run_post(record, SOURCES, make_deps(client, record, tmp_path))
    assert out.status == PostStatus.PARKED and "P10 call A" in out.history[-1]


def test_injection_flag_parks_never_fails(fixtures, record, tmp_path):
    fixtures[("P10", "A")] = {**fixtures[("P10", "A")], "injection_suspected": True}
    client = MockClient(fixtures)
    out = run_post(record, SOURCES, make_deps(client, record, tmp_path))
    assert out.status == PostStatus.PARKED and out.flags.injection_suspected
    assert out.gates.facts.passed  # the flag did not change any claim's status


# ---- drafter code checks (Pack P2) ---------------------------------------------------


def test_drafter_parks_on_uncovered_numeral(fixtures, record):
    fixtures[("P2", "2")] = ok(
        claims=[
            {
                "text": "no numbers here",
                "source_url": None,
                "source_quote": None,
                "in_test_log": True,
            }
        ],
        numeral_check=[],
    )
    with pytest.raises(AgentBlockedError, match="numerals"):
        draft(record, MockClient(fixtures), source_docs(), CHARACTER_BLOCK, TEST_LOG)


def test_drafter_nulls_source_when_quote_is_not_in_source(fixtures, record):
    bad = dict(fixtures[("P2", "2")])
    bad["claims"] = [dict(c) for c in bad["claims"]]
    bad["claims"][0]["source_quote"] = "this sentence is not on the page"
    fixtures[("P2", "2")] = bad
    out = draft(record, MockClient(fixtures), source_docs(), CHARACTER_BLOCK, TEST_LOG)
    assert out.draft.claims[0].source_url is None and out.draft.claims[0].source_quote is None
    assert out.draft.claims[1].source_url == SOURCES[0]["url"]
    assert out.draft.source_list == [SOURCES[0]["url"]]


def test_drafter_receives_disclosure_and_character_blocks(fixtures, record):
    client = MockClient(fixtures)
    draft(record, client, source_docs(), CHARACTER_BLOCK, TEST_LOG)
    c = next(c for c in client.calls if c.spec.prompt_id == "P2")
    texts = [b["text"] for b in c.system]
    assert any("<character_block" in t for t in texts) and any(
        "<disclosure_fields" in t for t in texts
    )
    assert "cache_control" in c.system[-1]


# ---- disclosure ----------------------------------------------------------------------


def test_mfk_unknown_factor_id_is_unscored_not_dropped(fixtures, record):
    fixtures[("P9", "default")] = ok(
        factors=[{"factor_id": "subject_matter", "outcome": "present"}]
    )
    gate = check_disclosure(record, MockClient(fixtures))
    assert gate.mfk_band == "unscored" and not gate.passed
    assert gate.mfk_factors == ["unknown_factor:subject_matter"]


def test_mfk_bands_and_heaviest_unclear_rule():
    assert (
        mfk_band(
            mfk_score(
                [("child_subject_matter", "present"), ("intended_audience_children", "present")]
            )
        )
        == "likely"
    )
    s7 = [
        ("child_characters_or_toys", "present"),
        ("child_oriented_music_or_animation", "present"),
        ("child_subject_matter", "absent"),
    ]
    assert mfk_band(mfk_score(s7), s7) == "doubtful"  # PRD S7: toys and animation on an adult topic
    unclear = [("child_subject_matter", "unclear")]
    assert mfk_band(mfk_score(unclear), unclear) == "doubtful"
    assert mfk_band(0.0, [("child_subject_matter", "absent")]) == "clear"


def test_mfk_doubtful_parks_for_hemant(fixtures, record, tmp_path):
    fixtures[("P9", "default")] = ok(
        factors=[{"factor_id": "child_subject_matter", "outcome": "unclear"}]
    )
    out = run_post(record, SOURCES, make_deps(MockClient(fixtures), record, tmp_path))
    assert out.status == PostStatus.PARKED and "doubtful" in out.history[-1]


def test_paid_promo_needs_ad_prefix(record, client):
    record.sponsor = Sponsor(sponsor_id="s1", name="NoteCo")
    record.variants = [
        Variant(platform=Platform.YOUTUBE_LONG, title="We tested", caption=AI_ROLE_DISCLOSURE)
    ]
    gate = check_disclosure(record, client)
    assert gate.paid_promo and not gate.ad_prefix_ok and not gate.passed
    record.variants = [
        Variant(
            platform=Platform.YOUTUBE_LONG,
            title="Ad: We tested",
            caption="Ad. " + AI_ROLE_DISCLOSURE,
        )
    ]
    assert check_disclosure(record, client).ad_prefix_ok


# ---- originality ---------------------------------------------------------------------


def test_originality_blocks_near_duplicate_and_skips_canaries():
    history = InMemoryHistory()
    prior = PostRecord(channel_id=Channel.A)
    prior.draft.script = (
        "We tested Notely for seven days. On day 3 the sync failed. Our test log shows 12 seconds."
    )
    history.add(prior)
    canary = PostRecord(channel_id=Channel.A)
    canary.flags.canary = True
    canary.draft.script = "canary text"
    history.add(canary)
    assert len(history.posts) == 1  # D23
    dup = PostRecord(channel_id=Channel.A)
    dup.draft.script = prior.draft.script + " One extra sentence about the test log."
    g = check_originality(dup, history, ["test log"])
    assert g.max_similarity > 0.85 and not g.passed and g.nearest_post_id == prior.post_id
    fresh = PostRecord(channel_id=Channel.B)  # history is cross-channel (PRD 4.12)
    fresh.draft.script = "Falak explains how reverse osmosis pushes seawater through a membrane; the test log is in the description."
    assert check_originality(fresh, history, ["test log"]).passed


def test_originality_history_window_and_arabic_tokens():
    history = InMemoryHistory()
    old = PostRecord(channel_id=Channel.C, created_at=datetime.now(UTC) - timedelta(days=120))
    old.draft.script = "تحلية المياه تستخدم الضغط لدفع الماء عبر غشاء. سجل الاختبار في الوصف."
    history.add(old)
    same = PostRecord(channel_id=Channel.C)
    same.draft.script = old.draft.script
    assert check_originality(same, history, ["سجل الاختبار"]).passed  # 120 days > 90-day window
    recent = PostRecord(channel_id=Channel.C)
    recent.draft.script = old.draft.script
    history.add(recent)
    g = check_originality(same, history, ["سجل الاختبار"])
    assert g.max_similarity > 0.85 and not g.passed  # Arabic tokenises; the duplicate is seen
