"""The screens (D38, D39): what each reviewer sees and what the server refuses,
driven through the HTML app with the demo seed. Every D33/D34 rule is asserted from
the rendered page, not the JSON, because the page is what a reviewer uses."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from cap.approval.app import Store, create_app
from cap.demo.seed import seed_store
from cap.schema.post_record import Channel, PostStatus
from cap.ui.tokens import is_current


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # the stand-in renderer writes under renders/
    store = Store()
    seed_store(store, demo=True, canary=True)
    api = TestClient(create_app(store), follow_redirects=False)
    api.get("/login?name=hemant")  # sets the signed cookie
    return store, api


def _post(store: Store, channel: Channel, canary: bool = False):
    return next(p for p in store.queue() if p.channel_id == channel and p.flags.canary == canary)


def test_tokens_css_is_generated_from_the_design_system():
    assert is_current(), "run `cap tokens build`; design/proof/tokens.json changed"


def test_sign_in_is_required_and_role_checked(world):
    store, api = world
    anon = TestClient(create_app(store), follow_redirects=False)
    assert anon.get("/").status_code == 401
    assert anon.get("/login?name=nobody").status_code == 403
    assert api.get("/").status_code == 200


def test_portfolio_marks_examples_and_names_sources(world):
    store, api = world
    html = api.get("/").text
    assert "example figures" in html
    assert "Sources:" in html and "YouTube Analytics" in html
    assert "Next kill gate" in html
    for absent in ("daily views", "subscriber delta", "trending"):
        assert absent not in html.lower()
    # with no analytics at all the screen says so instead of inventing
    store.analytics, store.analytics_note = {}, ""
    html = api.get("/").text
    assert "no pull yet" in html and "example figures" not in html


def test_queue_is_oldest_first_and_blind(world):
    store, api = world
    html = api.get("/queue").text
    first = html.index(_post(store, Channel.A).post_id)
    second = html.index(_post(store, Channel.B).post_id)
    assert first < second
    assert "Originality" not in html and "mfk" not in html.lower()


def test_blind_card_then_reveal_with_server_side_clock(world):
    store, api = world
    post = _post(store, Channel.A)
    html = api.get(f"/posts/{post.post_id}").text
    for leak in ("Originality and anchor", "machines thought", "Critic"):
        assert leak not in html
    assert "your opening line goes here" in html  # the drafter's slot, not the token
    assert "[[HEMANT_OPENING_LINE]]" not in html
    # approve without the line: refused with the card re-rendered, nothing recorded
    r = api.post(f"/posts/{post.post_id}/verdict", data={"decision": "approve"})
    assert r.status_code == 422 and "opening line" in r.text
    assert post.approval.pre_reveal_decision is None
    # a proper approve: the server's clock says it was too fast (seconds, not minutes)
    time.sleep(0.2)
    r = api.post(
        f"/posts/{post.post_id}/verdict",
        data={
            "decision": "approve",
            "opening_line": "Seven days, one surprise.",
            "spot_check_supported": "yes",
            "time_on_card_s": "9999",  # ignored: the browser never sets the clock
        },
    )
    assert r.status_code == 303
    assert post.approval.too_fast is True and post.approval.time_on_card_s < 5
    html = api.get(f"/posts/{post.post_id}").text
    assert "Your verdict, recorded" in html and "machines thought" in html
    assert "Seven days, one surprise." in html  # the line now sits in the script
    r = api.post(f"/posts/{post.post_id}/finalise", data={})
    assert r.status_code == 303 and post.status == PostStatus.APPROVED
    assert post.approval.token  # issued server-side
    assert post.approval.token not in api.get(f"/posts/{post.post_id}").text  # never shown


def test_reject_needs_a_chip_and_a_wrong_chip_on_a_canary_pauses(world):
    store, api = world
    canary = _post(store, Channel.A, canary=True)
    api.get(f"/posts/{canary.post_id}")
    r = api.post(f"/posts/{canary.post_id}/verdict", data={"decision": "reject"})
    assert r.status_code == 422 and "reason chip" in r.text
    r = api.post(
        f"/posts/{canary.post_id}/verdict", data={"decision": "reject", "reason_code": "language"}
    )
    assert r.status_code == 303
    html = api.get(f"/posts/{canary.post_id}").text
    assert "This was a canary" in html  # revealed only after the verdict
    r = api.post(f"/posts/{canary.post_id}/finalise", data={})
    assert r.headers["location"] == "/paused" and store.paused
    assert canary.flags.canary_outcome == "missed"
    # every screen carries the band; actions are refused; an approver reopens
    assert "Queue paused" in api.get("/queue").text
    other = _post(store, Channel.B)
    r = api.post(
        f"/posts/{other.post_id}/verdict", data={"decision": "reject", "reason_code": "other"}
    )
    assert r.headers["location"] == "/paused"
    assert "got through" in api.get("/paused").text
    api.post("/paused/reopen")
    assert not store.paused


def test_channel_c_waits_for_stage_one_and_layla_sees_only_her_queue(world):
    store, api = world
    c = _post(store, Channel.C)
    html = api.get(f"/posts/{c.post_id}").text
    assert 'aria-disabled="true"' in html and 'dir="ltr"' in html  # Hemant's chrome stays LTR
    assert "stage-one pass first" in api.get(f"/posts/{c.post_id}/approve").text
    # Layla
    layla = TestClient(create_app(store), follow_redirects=False)
    layla.get("/login?name=layla")
    assert layla.get("/").headers["location"] == "/queue"
    q = layla.get("/queue").text
    assert 'dir="rtl"' in q and 'class="tabs"' not in q and c.post_id in q
    assert _post(store, Channel.A).post_id not in q
    assert layla.get(f"/posts/{_post(store, Channel.A).post_id}").status_code == 403
    assert layla.get("/digest").status_code == 403
    page = layla.get(f"/posts/{c.post_id}").text
    assert "Originality" not in page and "النص" in page
    r = layla.post(f"/posts/{c.post_id}/stage-one", data={"decision": "approve", "notes": "سليم"})
    assert r.status_code == 303 and c.approval.stage_one.reviewer == "layla"
    assert c.post_id not in layla.get("/queue").text
    # Hemant now sees stage one and can approve
    assert "stage one: layla" in api.get("/queue").text
    assert api.get(f"/posts/{c.post_id}/approve").status_code == 200
    assert "aria-disabled" not in api.get(f"/posts/{c.post_id}").text
    # Hemant cannot record stage one; Layla cannot approve
    assert (
        api.post(f"/posts/{c.post_id}/stage-one", data={"decision": "approve"}).status_code == 403
    )
    assert (
        layla.post(f"/posts/{c.post_id}/verdict", data={"decision": "approve"}).status_code == 403
    )


def test_digest_and_operator_render_from_the_records(world):
    store, api = world
    b = _post(store, Channel.B)
    api.get(f"/posts/{b.post_id}")
    api.post(f"/posts/{b.post_id}/verdict", data={"decision": "reject", "reason_code": "other"})
    api.post(f"/posts/{b.post_id}/finalise", data={})
    d = api.get("/digest").text
    assert "Something else" in d and "above the 20% flag" in d
    o = api.get("/operator").text
    assert "layla" in o and "1 in 3" in o and "secret" in o.lower()
    assert "sk-" not in o and "api_key" not in o.lower()


def test_api_still_works_with_the_cookie(world):
    store, api = world
    assert api.get("/api/queue").status_code == 200
    assert api.get("/api/docs").status_code == 200
