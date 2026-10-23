from fastapi.testclient import TestClient

from cap.approval import release_job
from cap.config import get_settings
from cap.domain.token import ApprovalToken, verify
from cap.gates.disclosure import MFK_FACTOR_WEIGHTS
from cap.harness import run_all
from cap.models import MockClient
from cap.pipeline import run_post
from cap.schema.post_record import PostStatus
from cap.server import create_app
from cap.store import MemoryStore
from tests.conftest import SOURCES, make_deps, ok

H = {"X-Reviewer": "hemant"}


def _queued_post(client, record, tmp_path):
    return run_post(record, SOURCES, make_deps(client, record, tmp_path))


def test_api_full_approval_flow(client, record, tmp_path):
    store = MemoryStore()
    post = _queued_post(client, record, tmp_path)
    store.put(post)
    api = TestClient(create_app(store))

    assert api.get("/api/queue").status_code == 401  # no identity, no queue
    q = api.get("/api/queue", headers=H).json()
    assert len(q) == 1 and q[0]["gates"] is None  # blind (D33)

    # D34: approve without the opening line is refused, even without viewing the card
    r = api.post(
        f"/api/posts/{post.post_id}/verdict",
        headers=H,
        json={"decision": "approve"},
    )
    assert r.status_code == 422
    # and someone who is not an approver cannot record anything
    r = api.post(
        f"/api/posts/{post.post_id}/verdict",
        headers={"X-Reviewer": "intruder"},
        json={
            "decision": "approve",
            "opening_line": "x",
            "spot_check_supported": True,
        },
    )
    assert r.status_code == 403

    r = api.post(
        f"/api/posts/{post.post_id}/verdict",
        headers=H,
        json={
            "decision": "approve",
            "opening_line": "Notes that forget to sync are notes you never wrote.",
            "spot_check_supported": True,
        },
    )
    assert r.status_code == 200 and r.json()["gates_revealed"] is True

    r = api.post(f"/api/posts/{post.post_id}/finalise", headers=H, json={})
    assert r.status_code == 200 and r.json() == {
        "post_id": post.post_id,
        "status": "approved",
        "final_render_started": True,
    }
    release_job.wait(post.post_id, 30)
    assert api.get(f"/api/posts/{post.post_id}/release", headers=H).json() == {
        "post_id": post.post_id,
        "status": "approved",
        "release": "ready",
        "error": "",
        "token_issued": True,
    }
    stored = store.get(post.post_id)
    assert stored.status == PostStatus.APPROVED
    t = ApprovalToken.decode(stored.approval.token)
    assert verify(t, post.post_id, stored.release.manifest_sha256) == (True, "ok")

    # closed: nothing more can happen to it through the API
    assert (
        api.post(
            f"/api/posts/{post.post_id}/finalise",
            headers=H,
            json={"revised_decision": "reject", "reason_code": "other"},
        ).status_code
        == 422
    )
    assert api.get("/api/queue", headers={"X-Reviewer": "nobody"}).status_code == 403
    assert (
        api.post(
            f"/api/posts/{post.post_id}/verdict",
            headers=H,
            json={"decision": "reject", "reason_code": "other"},
        ).status_code
        == 422
    )


def test_api_rejected_post_cannot_be_flipped_to_approved(client, record, tmp_path):
    store = MemoryStore()
    post = _queued_post(client, record, tmp_path)
    store.put(post)
    api = TestClient(create_app(store))
    api.post(
        f"/api/posts/{post.post_id}/verdict",
        headers=H,
        json={"decision": "reject", "reason_code": "other"},
    )
    assert (
        api.post(f"/api/posts/{post.post_id}/finalise", headers=H, json={}).json()["status"]
        == "rejected"
    )
    r = api.post(
        f"/api/posts/{post.post_id}/finalise",
        headers=H,
        json={"revised_decision": "approve", "opening_line": "x", "spot_check_supported": True},
    )
    assert r.status_code == 422
    assert (
        store.get(post.post_id).status == PostStatus.REJECTED
        and store.get(post.post_id).approval.token == ""
    )


def test_api_identity_stand_in_is_opt_in(client, record, tmp_path, monkeypatch):
    monkeypatch.setenv("CAP_DEV_REVIEWER_HEADER", "false")
    get_settings.cache_clear()
    api = TestClient(create_app(MemoryStore()))
    assert api.get("/api/queue", headers=H).status_code == 503


def test_harness_reports_blocked_instead_of_a_percentage():
    reports = {
        r.name: r for r in run_all(MockClient({}), runs=2)
    }  # no fixtures: every judge blocked
    red = reports["red"]
    assert red.total == 20 and red.blocked > 0
    assert "BLOCKED" in red.summary() and "%" not in red.summary().split("repeatability")[0]
    assert reports["mfk"].blocked == 15 and "BLOCKED" in reports["mfk"].summary()
    assert reports["fact"].skipped and reports["inject"].skipped


def test_harness_scores_exact_and_non_miss_separately():
    fx = {
        ("P1", "A"): ok(verdict="pass", rule_findings=[]),
        ("P1", "B"): ok(verdict="pass", rule_findings=[]),
        ("P9", "default"): ok(
            factors=[{"factor_id": f, "outcome": "absent"} for f in MFK_FACTOR_WEIGHTS]
        ),
    }
    reports = {r.name: r for r in run_all(MockClient(fx), runs=2)}
    red, green, mfk = reports["red"], reports["green"], reports["mfk"]
    assert red.blocked == 0 and green.blocked == 0
    # passing judges: only the code-judged hard rules catch Red items; most are misses
    assert 0 < red.non_miss < 10 and red.exact <= red.non_miss
    assert green.exact == 20 and green.repeatability == 1.0
    assert "non-miss" in red.summary() and "rule coverage" in red.summary()
    # a clear scorer gets the five clear MFK items and nothing else
    assert mfk.exact == 5 and mfk.blocked == 0


def test_cli_demo_post_and_canary_seed():
    from cap.demo.seed import demo_post as _demo_post

    post = _demo_post()
    assert post.status == PostStatus.AWAITING_APPROVAL and post.flags.canary is False
    canary = _demo_post(canary=True)
    assert canary.flags.canary and canary.flags.planted_fault is not None
    assert canary.flags.canary_fault_type == canary.flags.planted_fault.type
