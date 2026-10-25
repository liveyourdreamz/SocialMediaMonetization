"""Try again from the screens (25 Oct: a Wi-Fi drop left a Falak draft stuck with no
way to restart it): a post that stopped on a failure goes back to the step it stopped
on, with the inputs it had; a post parked for a decision does not."""

from __future__ import annotations

from cap.demo.canary_job import file_brief
from cap.demo.retry import can_try_again, plan
from cap.demo.seed import new_brief
from cap.jobs import JobKind
from cap.schema.post_record import PostStatus
from cap.server import create_app
from cap.store import MemoryStore, park_if_interrupted
from cap.worker import Worker, drain
from tests.test_ui import Browser


def _failed_draft(store):  # type: ignore[no-untyped-def]
    rec = new_brief("B", "How monsoons form")
    file_brief(store, rec, {"urls": ["https://example.org/monsoon"], "test_log": ""})

    def no_network(store, job):  # type: ignore[no-untyped-def]
        raise ConnectionError("the Wi-Fi dropped")

    Worker(store, {JobKind.BRIEF: no_network}).run_once()  # CAP_JOB_RETRIES=0 in tests
    return rec.post_id


def test_a_failed_draft_is_tried_again_from_the_screen_and_reaches_the_queue(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = MemoryStore()
    pid = _failed_draft(store)
    parked = store.get(pid)
    assert parked.status == PostStatus.PARKED and can_try_again(parked)

    api = Browser(create_app(store), follow_redirects=False)
    api.sign_in("hemant")
    assert "Restart the draft step" in api.get("/operator").text
    assert "Restart the draft step" in api.get(f"/posts/{pid}").text
    r = api.form(f"/posts/{pid}/restart")
    assert r.status_code == 303
    jobs = store.jobs.jobs_for(pid)
    assert jobs[-1].kind == JobKind.BRIEF and jobs[-1].payload["urls"] == [
        "https://example.org/monsoon"
    ]
    assert api.form(f"/posts/{pid}/restart").status_code == 409  # already running

    drain(store, pid)
    got = store.get(pid)
    assert got.status == PostStatus.AWAITING_APPROVAL, got.history[-3:]
    assert any("trying again from the draft step, asked by hemant" in h for h in got.history)


def test_a_post_parked_for_a_decision_is_not_tried_again(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = MemoryStore()
    rec = new_brief("A", "How a pacemaker keeps time")
    rec.stop("checks", "injection suspected in a fetched page", PostStatus.PARKED)
    store.put(rec)
    assert not can_try_again(store.get(rec.post_id))
    api = Browser(create_app(store), follow_redirects=False)
    api.sign_in("hemant")
    assert "waits for a decision" in api.get("/operator").text
    assert api.form(f"/posts/{rec.post_id}/restart").status_code == 409


def test_an_interrupted_checks_step_resumes_at_checks_with_its_sources():
    store = MemoryStore()
    rec = new_brief("B", "How tides work")
    rec.status = PostStatus.GATED
    store.file(rec, JobKind.CHECKS, {"sources": [{"url": "https://example.org/t"}], "test_log": ""})
    job = store.jobs.claim()
    store.jobs.fail(job.id, "interrupted")
    current = store.get(rec.post_id)
    assert park_if_interrupted(current)
    store.put(current)
    kind, status, payload = plan(store.get(rec.post_id), store.jobs.jobs_for(rec.post_id))
    assert (kind, status) == (JobKind.CHECKS, PostStatus.GATED)
    assert payload["sources"] == [{"url": "https://example.org/t"}]


def test_signed_out_nobody_can_try_again(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = MemoryStore()
    pid = _failed_draft(store)
    anon = Browser(create_app(store), follow_redirects=False)
    anon.get("/login")
    assert anon.form(f"/posts/{pid}/restart").status_code in (303, 401, 403)
    assert store.get(pid).status == PostStatus.PARKED


# ---- a step that hangs (the Falak draft on 25 Oct) -------------------------------------


def _hung_draft(store, minutes=20):  # type: ignore[no-untyped-def]
    import datetime as dt

    rec = new_brief("B", "How monsoons form")
    file_brief(store, rec, {"urls": ["https://example.org/monsoon"], "test_log": ""})
    job = store.jobs.claim()  # a worker took it, then the Wi-Fi dropped
    store.jobs._jobs[job.id - 1].started_at = dt.datetime.now(dt.UTC) - dt.timedelta(
        minutes=minutes
    )
    return rec.post_id, job


def test_a_hung_step_shows_restart_on_that_step_and_runs_again(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = MemoryStore()
    pid, old = _hung_draft(store)
    api = Browser(
        create_app(store, run_jobs=False), follow_redirects=False
    )  # the hang is the test's
    api.sign_in("hemant")
    page = api.get(f"/posts/{pid}").text
    assert "Stuck at draft" in page and "Restart the draft step" in page
    assert "Restart the draft step" in api.get("/operator").text
    assert api.form(f"/posts/{pid}/restart").status_code == 303
    jobs = store.jobs.jobs_for(pid)
    assert [j.status.value for j in jobs] == ["failed", "queued"]
    assert jobs[-1].payload == old.payload  # the same inputs
    drain(store, pid)
    assert store.get(pid).status == PostStatus.AWAITING_APPROVAL


def test_a_step_still_within_its_time_is_not_offered_a_restart(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = MemoryStore()
    pid, _ = _hung_draft(store, minutes=3)
    api = Browser(
        create_app(store, run_jobs=False), follow_redirects=False
    )  # the hang is the test's
    api.sign_in("hemant")
    assert "Restart" not in api.get(f"/posts/{pid}").text
    assert api.form(f"/posts/{pid}/restart").status_code == 409


def test_the_replaced_worker_waking_up_later_changes_nothing():
    from cap.demo.retry import restart

    store = MemoryStore()
    pid, old = _hung_draft(store)
    restart(store, pid, "hemant")
    Worker(store, {JobKind.BRIEF: lambda s, j: None})._run(old)  # the old call finally returns
    statuses = [j.status.value for j in store.jobs.jobs_for(pid)]
    assert statuses == ["failed", "queued"]  # not finished, not retried: nothing runs twice
