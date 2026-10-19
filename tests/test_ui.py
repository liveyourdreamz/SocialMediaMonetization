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


class Browser(TestClient):
    """A TestClient that signs in with a POST and sends the CSRF token on every form."""

    def sign_in(self, name: str) -> None:
        r = self.post("/login", data={"name": name})
        assert r.status_code == 303, r.text

    def form(self, url: str, **data: str):
        data["csrf"] = self.cookies.get("cap_csrf", "")
        return self.post(url, data=data)


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # the stand-in renderer writes under renders/
    store = Store()
    seed_store(store, demo=True, canary=True)
    api = Browser(create_app(store), follow_redirects=False)
    api.sign_in("hemant")
    return store, api


def _post(store: Store, channel: Channel, canary: bool = False):
    return next(p for p in store.queue() if p.channel_id == channel and p.flags.canary == canary)


def test_tokens_css_is_generated_from_the_design_system():
    assert is_current(), "run `cap tokens build`; design/proof/tokens.json changed"


def test_sign_in_is_required_role_checked_and_csrf_enforced(world):
    store, api = world
    anon = Browser(create_app(store), follow_redirects=False)
    assert anon.get("/").headers["location"] == "/login"  # screens redirect, not JSON
    assert anon.post("/login", data={"name": "nobody"}).status_code == 403
    assert anon.get("/", headers={"X-Reviewer": "hemant"}).status_code == 303  # header is API-only
    assert api.get("/").status_code == 200
    post = _post(store, Channel.A)
    r = api.post(
        f"/posts/{post.post_id}/verdict", data={"decision": "reject", "reason_code": "other"}
    )
    assert r.status_code == 403 and post.approval.pre_reveal_decision is None  # no CSRF token
    assert api.form(f"/posts/{post.post_id}/verdict", decision="maybe").status_code == 422


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
    r = api.form(f"/posts/{post.post_id}/verdict", decision="approve")
    assert r.status_code == 422 and "opening line" in r.text
    assert post.approval.pre_reveal_decision is None
    # a proper approve: the server's clock says it was too fast (seconds, not minutes)
    time.sleep(0.2)
    r = api.form(
        f"/posts/{post.post_id}/verdict",
        decision="approve",
        opening_line="Seven days, one surprise.",
        spot_check_supported="yes",
        time_on_card_s="9999",  # ignored: the browser never sets the clock
    )
    assert r.status_code == 303
    assert post.approval.too_fast is True and post.approval.time_on_card_s < 5
    html = api.get(f"/posts/{post.post_id}").text
    assert "Your verdict, recorded" in html and "machines thought" in html
    assert "Seven days, one surprise." in html  # the line now sits in the script
    r = api.form(f"/posts/{post.post_id}/finalise")
    assert r.status_code == 303 and post.status == PostStatus.APPROVED
    assert post.approval.token  # issued server-side
    assert post.approval.token not in api.get(f"/posts/{post.post_id}").text  # never shown


def test_reject_needs_a_chip_and_a_wrong_chip_on_a_canary_pauses(world):
    store, api = world
    canary = _post(store, Channel.A, canary=True)
    api.get(f"/posts/{canary.post_id}")
    r = api.form(f"/posts/{canary.post_id}/verdict", decision="reject")
    assert r.status_code == 422 and "reason chip" in r.text
    r = api.form(f"/posts/{canary.post_id}/verdict", decision="reject", reason_code="language")
    assert r.status_code == 303
    html = api.get(f"/posts/{canary.post_id}").text
    assert "machines thought" in html and "This was a canary" not in html  # fault stays hidden
    r = api.form(f"/posts/{canary.post_id}/finalise")
    assert r.headers["location"] == "/paused" and store.paused
    assert canary.flags.canary_outcome == "missed"
    # every screen carries the band; actions are refused; an approver reopens
    assert "Queue paused" in api.get("/queue").text
    other = _post(store, Channel.B)
    r = api.form(f"/posts/{other.post_id}/verdict", decision="reject", reason_code="other")
    assert r.headers["location"] == "/paused"
    assert "got through" in api.get("/paused").text
    api.form("/paused/reopen")
    assert not store.paused
    # closed now: the planted fault is on the record
    assert "This was a canary" in api.get(f"/posts/{canary.post_id}").text


def test_channel_c_waits_for_stage_one_and_layla_sees_only_her_queue(world):
    store, api = world
    c = _post(store, Channel.C)
    html = api.get(f"/posts/{c.post_id}").text
    assert "disabled>Approve" in html and 'dir="ltr"' in html  # Hemant's chrome stays LTR
    assert "stage-one pass first" in api.get(f"/posts/{c.post_id}/approve").text
    # Layla
    layla = Browser(create_app(store), follow_redirects=False)
    layla.sign_in("layla")
    assert layla.get("/").headers["location"] == "/queue"
    q = layla.get("/queue").text
    assert 'dir="rtl"' in q and 'class="tabs"' not in q and c.post_id in q
    assert _post(store, Channel.A).post_id not in q
    assert layla.get(f"/posts/{_post(store, Channel.A).post_id}").status_code == 403
    assert layla.get("/digest").status_code == 403
    page = layla.get(f"/posts/{c.post_id}").text
    assert "Originality" not in page and "النص" in page
    r = layla.form(f"/posts/{c.post_id}/stage-one", decision="approve", notes="سليم")
    assert r.status_code == 303 and c.approval.stage_one.reviewer == "layla"
    assert c.post_id not in layla.get("/queue").text
    # Hemant now sees stage one and can approve
    assert "stage one: layla" in api.get("/queue").text
    assert api.get(f"/posts/{c.post_id}/approve").status_code == 200
    assert "disabled>Approve" not in api.get(f"/posts/{c.post_id}").text
    # Hemant cannot record stage one; Layla cannot approve
    assert api.form(f"/posts/{c.post_id}/stage-one", decision="approve").status_code == 403
    assert layla.form(f"/posts/{c.post_id}/verdict", decision="approve").status_code == 403


def test_digest_and_operator_render_from_the_records(world):
    store, api = world
    b = _post(store, Channel.B)
    api.get(f"/posts/{b.post_id}")
    api.form(f"/posts/{b.post_id}/verdict", decision="reject", reason_code="other")
    api.form(f"/posts/{b.post_id}/finalise")
    d = api.get("/digest").text
    assert "Something else" in d and "above the 20% flag" in d
    o = api.get("/operator").text
    assert "layla" in o and "1 in 3" in o and "secret" in o.lower()
    assert "sk-" not in o and "api_key" not in o.lower()


def test_api_enforces_roles_and_the_server_clock(world):
    store, api = world
    assert api.get("/api/queue").status_code == 200
    assert api.get("/api/docs").status_code == 200  # local only
    layla = Browser(create_app(store), follow_redirects=False)
    layla.sign_in("layla")
    q = layla.get("/api/queue").json()
    assert {c["channel_id"] for c in q} == {"C"} and all(c["gates"] is None for c in q)
    a = _post(store, Channel.A)
    assert layla.get(f"/api/posts/{a.post_id}/card").status_code == 403
    assert (
        layla.post(f"/api/posts/{a.post_id}/verdict", json={"decision": "approve"}).status_code
        == 403
    )
    # the browser cannot set the clock through the API either
    b = _post(store, Channel.B)
    api.get(f"/api/posts/{b.post_id}/card")
    r = api.post(
        f"/api/posts/{b.post_id}/verdict",
        json={"decision": "reject", "reason_code": "other", "time_on_card_s": 9999},
    )
    assert r.status_code == 200 and b.approval.time_on_card_s < 5 and b.approval.too_fast


def test_canary_is_scored_blind_even_if_revised_with_the_answer_in_hand(world):
    store, api = world
    canary = _post(store, Channel.A, canary=True)
    api.get(f"/posts/{canary.post_id}")
    api.form(f"/posts/{canary.post_id}/verdict", decision="reject", reason_code="language")
    html = api.get(f"/posts/{canary.post_id}").text
    assert "This was a canary" not in html  # the planted fault stays hidden until closed
    r = api.form(
        f"/posts/{canary.post_id}/finalise",
        revised_decision="reject",
        reason_code="out_group_title",
    )
    assert r.headers["location"] == "/paused"
    assert canary.flags.canary_outcome == "missed" and store.paused


def test_a_refused_revision_leaves_the_record_intact(world):
    store, api = world
    c = _post(store, Channel.C)
    api.get(f"/posts/{c.post_id}")
    # blind approve on C without stage one is refused outright (D19)
    r = api.form(
        f"/posts/{c.post_id}/verdict",
        decision="approve",
        opening_line="x",
        spot_check_supported="yes",
    )
    assert r.status_code == 422 and c.approval.pre_reveal_decision is None
    api.form(f"/posts/{c.post_id}/verdict", decision="reject", reason_code="language")
    r = api.form(
        f"/posts/{c.post_id}/finalise",
        revised_decision="approve",
        opening_line="x",
        spot_check_supported="yes",
    )
    assert r.status_code == 422
    assert (
        c.approval.decision == c.approval.pre_reveal_decision
        and not c.approval.revised_after_reveal
    )
    r = api.form(f"/posts/{c.post_id}/finalise")  # keep reject still works
    assert r.status_code == 303 and c.status == PostStatus.REJECTED and c.approval.decided_at


def test_every_post_form_carries_the_csrf_field(world):
    """A form without the token is a form that cannot be submitted."""
    import re

    store, api = world
    post = _post(store, Channel.A)
    pages = [api.get("/").text, api.get("/queue").text, api.get(f"/posts/{post.post_id}").text]
    pages.append(api.get(f"/posts/{post.post_id}/approve").text)
    for html in pages:
        for form in re.findall(r"<form[^>]*method=\"post\".*?</form>", html, flags=re.S):
            assert 'name="csrf"' in form, form[:120]
