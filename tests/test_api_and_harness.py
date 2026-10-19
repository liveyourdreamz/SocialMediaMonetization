from fastapi.testclient import TestClient

from cap.approval.app import Store, create_app
from cap.approval.token import ApprovalToken, verify
from cap.harness import run_all
from cap.models import MockClient
from cap.pipeline import PipelineDeps, run_post
from cap.schema.post_record import PostStatus
from tests.conftest import SOURCES


def _queued_post(client, record, tmp_path):
    deps = PipelineDeps(
        client=client, anchor_elements={"A": ["test log"]}, character_blocks={"A": "Patch"}
    )
    deps.render_dir = tmp_path
    return run_post(record, SOURCES, deps)


def test_api_full_approval_flow(client, record, tmp_path):
    store = Store()
    post = _queued_post(client, record, tmp_path)
    store.put(post)
    api = TestClient(create_app(store))
    h = {"X-Reviewer": "hemant"}

    assert api.get("/queue").status_code == 401  # no identity, no queue
    q = api.get("/queue", headers=h).json()
    assert len(q) == 1 and q[0]["gates"] is None  # blind (D33)

    # D34: approve without the opening line is refused
    r = api.post(
        f"/posts/{post.post_id}/verdict",
        headers=h,
        json={"decision": "approve", "time_on_card_s": 300},
    )
    assert r.status_code == 422

    r = api.post(
        f"/posts/{post.post_id}/verdict",
        headers=h,
        json={
            "decision": "approve",
            "time_on_card_s": 300,
            "opening_line": "Notes that forget to sync are notes you never wrote.",
            "spot_check_supported": True,
        },
    )
    assert r.status_code == 200 and r.json()["gates_revealed"] is True

    r = api.post(f"/posts/{post.post_id}/finalise", headers=h, json={})
    assert r.status_code == 200 and r.json() == {
        "post_id": post.post_id,
        "status": "approved",
        "token_issued": True,
    }
    stored = store.get(post.post_id)
    assert stored.status == PostStatus.APPROVED
    t = ApprovalToken.decode(stored.approval.token)
    assert verify(t, post.post_id, stored.asset.sha256) == (True, "ok")


def test_harness_runs_on_mock_and_reports_skips():
    # A mock that passes everything: Red recall is near 0, Green is 100. The point of this
    # test is the plumbing, not the number; live numbers need the real backend.
    fx = {
        ("P1", "A"): {"status": "ok", "verdict": "pass", "rule_findings": [], "explanation": ""},
        ("P1", "B"): {"status": "ok", "verdict": "pass", "rule_findings": [], "explanation": ""},
        ("P9", "default"): {"status": "ok", "factors": [], "explanation": ""},
    }
    reports = {r.name: r for r in run_all(MockClient(fx), runs=2)}
    # the code-judged hard rules (R-05 bait, R-07 disclosure) still catch a few Red
    # items with no judge at all; the rest need the real judges
    assert reports["red"].total == 20 and 0.0 < reports["red"].accuracy < 0.5
    assert reports["green"].total == 20 and reports["green"].accuracy == 1.0
    assert reports["green"].repeatability == 1.0
    assert reports["mfk"].total == 15
    assert reports["fact"].skipped and reports["inject"].skipped
