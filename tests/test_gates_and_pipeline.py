from cap.gates import InMemoryHistory, check_ethics, check_facts, check_originality
from cap.gates.ethics import aggregate, verdict_from_findings
from cap.models import MockClient
from cap.pipeline import PipelineDeps, run_post
from cap.schema.post_record import (
    Channel,
    Claim,
    JudgeResult,
    PostRecord,
    PostStatus,
    RuleFinding,
    RuleOutcome,
    Verdict,
)
from tests.conftest import SOURCES, ok, source_docs


def _deps(client):
    return PipelineDeps(
        client=client,
        anchor_elements={"A": ["test log", "day 3"]},
        character_blocks={"A": "Patch: AI tools tester. Catchphrase cap 1."},
    )


def test_happy_path_reaches_queue(client, record, tmp_path):
    deps = _deps(client)
    deps.render_dir = tmp_path
    out = run_post(record, SOURCES, deps)
    assert out.status == PostStatus.AWAITING_APPROVAL, out.history
    assert out.gates.all_passed
    assert out.asset.sha256 and (tmp_path / f"{out.post_id}.render.txt").exists()
    assert len(out.variants) == 2
    # the judges were called once per variant, isolated (D26): four P1 calls
    p1 = [c for c in client.calls if c.spec.prompt_id == "P1"]
    assert len(p1) == 4 and all("rule_findings" not in c.user for c in p1)
    # sources only ever reach a model as tool results inside this call's nonce tags
    for c in client.calls:
        assert SOURCES[0]["body"] not in c.user and SOURCES[0]["body"] not in c.user_tail
        for block in c.tool_result_blocks():
            assert f'<untrusted_text id="{c.nonce}">' in block["text"]
        assert c.nonce in c.system[0]["text"]  # the same nonce the rule names


def test_judge_disagreement_escalates_and_parks(fixtures, record, tmp_path):
    fixtures[("P1", "B")] = ok(
        verdict="escalate",
        rule_findings=[{"rule_id": "R-16", "outcome": "escalate", "evidence_span": "our test"}],
    )
    client = MockClient(fixtures)
    deps = _deps(client)
    deps.render_dir = tmp_path
    out = run_post(record, SOURCES, deps)
    assert out.status == PostStatus.PARKED
    assert out.gates.ethics.verdict == Verdict.ESCALATE and out.gates.ethics.disagreement


def test_hard_rule_fires_before_judges(record, client):
    record.draft.caption = (
        "Comment YES if you agree and tag 3 friends"  # R-05, no AI-role disclosure (R-07)
    )
    record.draft.script = "x"
    gate = check_ethics(record, client)
    assert gate.verdict == Verdict.FAIL
    assert {"R-05", "R-07"} <= set(gate.rule_ids)
    assert not [c for c in client.calls if c.spec.prompt_id == "P1"]  # judges never called


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
                from cap.models import Completion

                return Completion(raw="", stop_reason="refusal", stop_category="policy")
            return super()._complete(call)

    record.draft.caption = "Presented by an AI character."  # keep R-07 quiet so judges run
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
                from cap.models import Completion

                return Completion(raw="not json", stop_reason="end_turn")
            return super()._complete(call)

    from cap.gates import check_disclosure

    gate = check_disclosure(record, Broken(fixtures))
    assert seen["n"] == 2 and gate.passed is False and gate.mfk_factors == ["p9_unavailable"]


def test_fact_quote_must_be_exact_substring(fixtures, record):
    fixtures[("P10", "B")] = ok(
        claim_status="supported", supporting_quote="Sync completes within ten seconds", note=""
    )
    client = MockClient(fixtures)
    record.draft.claims = [
        Claim(text="sync completes within 10 seconds", source_url=SOURCES[0]["url"])
    ]
    fixtures[("P10", "A")] = ok(
        claims=[{"text": record.draft.claims[0].text, "source_url": SOURCES[0]["url"]}],
        added_claims=[],
        endorsement_wording=[],
    )
    gate, _ = check_facts(record, client, source_docs())
    assert not gate.passed and gate.claim_checks[0].final == "unsupported"
    assert gate.claim_checks[0].quote_in_source is False


def test_second_verifier_disagreement_blocks(fixtures, record):
    fixtures[("P10", "C")] = ok(claim_status="unsupported", supporting_quote="", note="stretch")
    client = MockClient(fixtures)
    record.draft.claims = [
        Claim(text="Notely stores notes locally first, then syncs", source_url=SOURCES[0]["url"])
    ]
    fixtures[("P10", "A")] = ok(
        claims=[{"text": record.draft.claims[0].text, "source_url": SOURCES[0]["url"]}],
        added_claims=[],
        endorsement_wording=[],
    )
    gate, _ = check_facts(record, client, source_docs())
    assert not gate.passed and gate.claim_checks[0].disagreement
    assert "second verifier disagreed" in gate.claim_checks[0].note


def test_originality_blocks_near_duplicate_and_skips_canaries():
    history = InMemoryHistory(posts=[])
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
    dup.draft.script = prior.draft.script
    g = check_originality(dup, history, ["test log"])
    assert g.max_similarity > 0.85 and not g.passed and g.nearest_post_id == prior.post_id
    fresh = PostRecord(channel_id=Channel.A)
    fresh.draft.script = "Falak explains how reverse osmosis pushes seawater through a membrane; the test log is in the description."
    assert check_originality(fresh, history, ["test log"]).passed


def test_injection_flag_parks_never_fails(fixtures, record, tmp_path):
    fixtures[("P10", "A")] = {**fixtures[("P10", "A")], "injection_suspected": True}
    client = MockClient(fixtures)
    deps = _deps(client)
    deps.render_dir = tmp_path
    out = run_post(record, SOURCES, deps)
    assert out.status == PostStatus.PARKED and out.flags.injection_suspected
    assert out.gates.facts.passed  # the flag did not change any claim's status
