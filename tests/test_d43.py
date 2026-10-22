"""D43 (22 Oct 2026, Hemant: "I do not want my platform to stop in this process"):
mechanical problems are repaired by code; questions a gate cannot settle go to the
card for Hemant; only outright failures reject."""

import pytest

from cap.agents import OPENING_LINE_SLOT, adapt, critique, draft
from cap.approval import token as tok
from cap.approval.card import CardError, finalise, record_verdict
from cap.demo import CHARACTER_BLOCK, TEST_LOG
from cap.models import MockClient
from cap.pipeline import run_post
from cap.publisher import DryRunAdapter, PublishRefusedError, publish
from cap.schema.post_record import Decision, Platform, PostStatus, Verdict
from tests.conftest import SOURCES, make_deps, ok, source_docs


def _escalated(fixtures, record, tmp_path):
    fixtures[("P1", "B")] = ok(
        verdict="escalate",
        rule_findings=[{"rule_id": "R-16", "outcome": "escalate", "evidence_span": "our test"}],
    )
    out = run_post(record, SOURCES, make_deps(MockClient(fixtures), record, tmp_path))
    assert out.status == PostStatus.AWAITING_APPROVAL and out.flags.needs_decision
    return out


def _approve(rec, **kw):
    record_verdict(
        rec,
        reviewer="hemant",
        decision=Decision.APPROVE,
        opening_line="Here is the thing.",
        spot_check_supported=True,
        time_on_card_s=120,
    )
    return finalise(rec, reviewer="hemant", **kw)


def test_approving_an_escalated_post_needs_the_tick(fixtures, record, tmp_path):
    rec = _escalated(fixtures, record, tmp_path)
    with pytest.raises(CardError, match="questions only you can settle"):
        _approve(rec.model_copy(deep=True))
    done = _approve(rec, acknowledged=True)
    assert done.status == PostStatus.APPROVED
    assert done.approval.decisions_acknowledged == done.flags.needs_decision
    assert done.gates_cleared


def test_the_publisher_takes_a_settled_post_and_refuses_an_unsettled_one(
    fixtures, record, tmp_path
):
    rec = _escalated(fixtures, record, tmp_path)
    asset = tmp_path / "ep.txt"
    asset.write_text("render")
    rec.asset.uri, rec.asset.sha256 = str(asset), tok.sha256_file(asset)
    rec.status = PostStatus.APPROVED
    t = tok.issue(rec.post_id, rec.asset.sha256, "hemant", "approve", canary=False)
    with pytest.raises(PublishRefusedError, match="settled on the card"):
        publish(rec.model_copy(deep=True), asset, t, DryRunAdapter())
    rec.approval.decisions_acknowledged = list(rec.flags.needs_decision)
    assert publish(rec, asset, t, DryRunAdapter()).status == PostStatus.PUBLISHED


def test_an_outright_ethics_fail_still_rejects(fixtures, record, tmp_path):
    fixtures[("P1", "A")] = ok(
        verdict="fail",
        rule_findings=[{"rule_id": "R-01", "outcome": "block", "evidence_span": "them"}],
    )
    out = run_post(record, SOURCES, make_deps(MockClient(fixtures), record, tmp_path))
    assert out.status == PostStatus.REJECTED and out.gates.ethics.verdict == Verdict.FAIL
    assert not out.gates_cleared


def test_a_missing_signature_phrase_is_added_not_rejected(fixtures, record, tmp_path):
    call1 = dict(fixtures[("P2", "1")])
    d = dict(call1["draft"])
    d["script"] = d["script"].replace("test log", "notes").replace("Patch", "the robot")
    d["title"] = "Notely, seven days"
    call1["draft"] = d
    fixtures[("P2", "1")] = call1
    deps = make_deps(MockClient(fixtures), record, tmp_path)
    deps.anchor_elements = {"A": ["take it apart"]}
    out = run_post(record, SOURCES, deps)
    assert "Let's take it apart." in out.draft.script, out.history[-4:]
    assert out.gates.originality.passed
    assert any("added the signature opener" in h for h in out.history)


@pytest.mark.parametrize(
    "script", ["No slot here.", f"{OPENING_LINE_SLOT} a {OPENING_LINE_SLOT} b"]
)
def test_the_opening_slot_is_repaired_to_exactly_one(fixtures, record, script):
    call1 = dict(fixtures[("P2", "1")])
    call1["draft"] = {**call1["draft"], "script": script}
    fixtures[("P2", "1")] = call1
    fixtures[("P2", "2")] = ok(claims=[], numeral_check=[])
    out = draft(record, MockClient(fixtures), source_docs(), CHARACTER_BLOCK, TEST_LOG)
    assert out.draft.script.count(OPENING_LINE_SLOT) == 1
    assert out.draft.script.startswith(OPENING_LINE_SLOT)


def test_the_drafter_gets_one_retry_with_the_reason(fixtures, record):
    good = fixtures[("P2", "1")]
    replies = [{"status": "blocked", "blocked_reason": "length unknown"}, good]
    fixtures[("P2", "1")] = lambda call: replies.pop(0)
    client = MockClient(fixtures)
    draft(record, client, source_docs(), CHARACTER_BLOCK, TEST_LOG)
    p2 = [c for c in client.calls if c.spec.prompt_id == "P2" and c.spec.call == "1"]
    assert len(p2) == 2 and "length unknown" in p2[1].user


def test_a_critic_that_declines_keeps_the_draft(fixtures, record):
    fixtures[("P3", "default")] = {"status": "blocked", "blocked_reason": "rubric unclear"}
    rec, accepted = critique(record, MockClient(fixtures), CHARACTER_BLOCK)
    assert accepted and any("critic declined" in h for h in rec.history)


def test_an_adapter_that_declines_gets_variants_built_from_the_draft(fixtures, record):
    record.draft.title, record.draft.caption = "Silk", "How silk works."
    record.draft.script = f"{OPENING_LINE_SLOT} A caterpillar spins silk."
    record.brief.length_s = 30
    fixtures[("P7", "default")] = {"status": "blocked", "blocked_reason": "limits missing"}
    out = adapt(record, MockClient(fixtures), [Platform.YOUTUBE_LONG, Platform.YOUTUBE_SHORT])
    assert [v.platform for v in out.variants] == [Platform.YOUTUBE_LONG, Platform.YOUTUBE_SHORT]
    assert all("ai character" in v.caption.lower() for v in out.variants)
    assert OPENING_LINE_SLOT not in out.variants[1].script_cut


def test_the_card_shows_the_question_and_needs_the_tick(fixtures, record, tmp_path, monkeypatch):
    from cap.approval.app import Store
    from tests.test_ui import Browser, create_app

    monkeypatch.chdir(tmp_path)
    rec = _escalated(fixtures, record, tmp_path)
    store = Store()
    store.put(rec)
    api = Browser(create_app(store), follow_redirects=False)
    api.sign_in("hemant")
    api.form(
        f"/posts/{rec.post_id}/verdict",
        decision="approve",
        opening_line="Here is the thing.",
        spot_check_supported="yes",
    )
    page = api.get(f"/posts/{rec.post_id}").text
    assert "You decide" in page and "R-16" in page and 'name="acknowledged"' in page
    r = api.form(f"/posts/{rec.post_id}/finalise")
    assert "questions only you can settle" in r.text
    assert store.get(rec.post_id).status == PostStatus.AWAITING_APPROVAL
    api.form(f"/posts/{rec.post_id}/finalise", acknowledged="yes")
    assert store.get(rec.post_id).status == PostStatus.APPROVED


# ---- made-for-kids bands (regression run 22 Oct: 10/15 -> 14/15) -------------------


def test_visuals_alone_never_make_a_post_likely_made_for_kids():
    from cap.gates.disclosure import mfk_band, mfk_score

    visuals = [
        ("child_subject_matter", "absent"),
        ("intended_audience_children", "absent"),
        ("child_characters_or_toys", "present"),
        ("child_oriented_language", "present"),
        ("child_oriented_music_or_animation", "present"),
        ("child_celebrities_or_influencers", "present"),
    ]
    assert mfk_band(mfk_score(visuals), visuals) == "clear"
    unsure = [("child_subject_matter", "unclear"), ("intended_audience_children", "unclear")]
    unsure += visuals[2:]
    assert mfk_band(mfk_score(unsure), unsure) == "doubtful"  # Hemant decides
    kids = [("child_subject_matter", "present"), ("intended_audience_children", "present")]
    kids += visuals[2:]
    assert mfk_band(mfk_score(kids), kids) == "likely"
