"""This week's ideas on the Digest: pick files a brief, reject records, both once (D48)."""

from __future__ import annotations

import datetime as dt

from cap.demo.seed import seed_store
from cap.ideas.week import monday_of
from cap.jobs import JobKind
from cap.schema.ideas import Idea, IdeaStatus
from cap.server import create_app
from cap.store import MemoryStore
from tests.test_ui import Browser


def world(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = MemoryStore()
    seed_store(store)
    monday = monday_of(dt.datetime.now(dt.UTC).date())
    ideas = [
        Idea(channel="A", week=monday, rank=1, topic="Why solar panels keep getting cheaper",
             angle="prices fell by nine tenths", demand_title="Perovskite solar cell",
             views_7d=41_200, ratio_to_baseline=3.1,
             evidence_urls=["https://ourworldindata.org/cheap-renewables-growth"],
             score=0.82, revenue_low_usd=10, revenue_high_usd=100),
        Idea(channel="A", week=monday, rank=2, topic="<script>alert(1)</script> How tides work",
             risk_lane="high"),
        Idea(channel="A", week=monday, rank=0, topic="Held story", status=IdeaStatus.HELD),
    ]  # fmt: skip
    store.ideas.add(ideas)
    api = Browser(create_app(store), follow_redirects=False)
    api.sign_in("hemant")
    return store, api, ideas


def test_the_digest_shows_this_weeks_ideas_ranked_and_escaped(tmp_path, monkeypatch):
    store, api, ideas = world(tmp_path, monkeypatch)
    page = api.get("/digest").text
    assert "Why solar panels keep getting cheaper" in page
    assert "41,200 views this week, 3.1x its usual" in page
    assert "$10 to $100 a post (assumed views)" in page and "high-risk lane" in page
    assert "<script>alert(1)</script>" not in page  # outside text is escaped
    assert "Held story" not in page  # held ideas are never shown
    assert page.index("Why solar panels") < page.index("How tides work")


def test_pick_files_the_brief_with_the_edited_title_once(tmp_path, monkeypatch):
    store, api, ideas = world(tmp_path, monkeypatch)
    r = api.form(f"/ideas/{ideas[0].idea_id}/pick", topic="How solar got so cheap")
    assert r.status_code == 303 and r.headers["location"] == "/operator"
    picked = store.ideas.get(ideas[0].idea_id)
    assert picked.status == IdeaStatus.PICKED and picked.topic_edit == "How solar got so cheap"
    post = store.get(picked.post_id)
    assert post.brief.topic == "How solar got so cheap" and post.channel_id.value == "A"
    assert store.jobs.active(post.post_id, JobKind.BRIEF)
    assert api.form(f"/ideas/{ideas[0].idea_id}/pick").status_code == 409  # never twice


def test_reject_records_and_a_reviewer_without_rights_cannot(tmp_path, monkeypatch):
    store, api, ideas = world(tmp_path, monkeypatch)
    assert api.form(f"/ideas/{ideas[1].idea_id}/reject").status_code == 303
    assert store.ideas.get(ideas[1].idea_id).status == IdeaStatus.REJECTED
    assert api.form("/ideas/nope/pick").status_code == 404
    arabic = Browser(create_app(store), follow_redirects=False)
    arabic.sign_in("layla")
    assert arabic.form(f"/ideas/{ideas[0].idea_id}/pick").status_code == 403
    assert api.post(f"/ideas/{ideas[0].idea_id}/pick", data={}).status_code in (400, 403)  # no CSRF


def test_run_now_queues_every_channel_and_operator_shows_the_runs(tmp_path, monkeypatch):
    store, api, _ = world(tmp_path, monkeypatch)
    assert "No runs yet." in api.get("/operator").text
    assert api.form("/operator/ideas").status_code == 303
    monday = monday_of(dt.datetime.now(dt.UTC).date())
    for ch in ("A", "B", "C"):
        assert store.jobs.active(f"ideas-{ch}-{monday.isoformat()}", JobKind.IDEAS)
    store.audit.append(
        "ideas run", detail={"report": "channel A, week of 26 Oct: 15 ideas, 4 held"}
    )
    assert "15 ideas, 4 held" in api.get("/operator").text


def test_a_pick_can_become_the_canary_and_looks_like_any_pick(tmp_path, monkeypatch, canaries):
    """Review R2: the canary is the post Hemant asked for, so nothing tells it apart."""
    store, api, ideas = world(tmp_path, monkeypatch)
    store.update_state(lambda s: setattr(s, "canary_gap", 1))
    for _ in range(2):
        from cap.schema.post_record import Channel, PostRecord, PostStatus

        p = PostRecord(channel_id=Channel.B, status=PostStatus.APPROVED)
        p.approval.decided_at = dt.datetime.now(dt.UTC)
        store.put(p)
    before = api.get("/operator").text.lower().count("canary")
    api.form(f"/ideas/{ideas[0].idea_id}/pick")
    picked = store.ideas.get(ideas[0].idea_id)
    assert picked.status == IdeaStatus.PICKED  # stays a pick; nothing leaves the Digest
    post = store.get(picked.post_id)
    assert post.flags.canary and store.jobs.active(post.post_id, JobKind.CANARY)
    page = api.get("/operator").text
    assert ideas[0].topic in page  # under "Your posts" like any brief
    assert page.lower().count("canary") == before  # the page says nothing new
