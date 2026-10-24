"""The worker runs the queue and survives restarts (M1 step 3, ADR-006, D43).

A "restart" here is what happens on the laptop: the app stops with a job half done
(claimed, never finished), and a new worker starts on the same store."""

from __future__ import annotations

import pytest

from cap.demo.seed import new_brief
from cap.jobs import MAX_ATTEMPTS, JobKind, JobStatus
from cap.schema.post_record import Channel, PostRecord, PostStatus, ReleaseStatus
from cap.store import MemoryStore
from cap.worker import Worker, drain


@pytest.fixture(autouse=True)
def _video_free(monkeypatch):
    monkeypatch.setenv("CAP_RENDERER", "text")
    from cap.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _filed(store) -> PostRecord:
    rec = new_brief("A", "A week with a note app")
    store.put(rec)
    store.jobs.enqueue(JobKind.BRIEF, rec.post_id, {"urls": [], "test_log": ""})
    return rec


def _crash_mid_job(store) -> None:
    """A worker takes the job and the app stops before it finishes."""
    assert store.jobs.claim() is not None


def test_a_brief_interrupted_by_a_restart_resumes_and_reaches_the_queue(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = MemoryStore()
    rec = _filed(store)
    _crash_mid_job(store)
    Worker(store).recover()  # the app starts again
    drain(store, rec.post_id)
    got = store.get(rec.post_id)
    assert got.status == PostStatus.AWAITING_APPROVAL, got.history[-3:]
    assert any("resumed after a restart: brief" in h for h in got.history)
    jobs = store.jobs.jobs_for(rec.post_id)
    assert [(j.kind, j.status) for j in jobs] == [
        (JobKind.BRIEF, JobStatus.DONE),
        (JobKind.CHECKS, JobStatus.DONE),
        (JobKind.RENDER, JobStatus.DONE),
    ]


def test_a_crash_during_the_checks_resumes_at_the_checks_not_the_writing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = MemoryStore()
    rec = _filed(store)
    assert Worker(store).run_once()  # the writing step finishes and queues the checks
    written = store.get(rec.post_id).draft.script
    _crash_mid_job(store)  # the checks start; the app stops
    Worker(store).recover()
    drain(store, rec.post_id)
    got = store.get(rec.post_id)
    assert got.status == PostStatus.AWAITING_APPROVAL and got.draft.script.endswith(written[-40:])
    attempts = {j.kind: j.attempts for j in store.jobs.jobs_for(rec.post_id)}
    assert attempts == {JobKind.BRIEF: 1, JobKind.CHECKS: 2, JobKind.RENDER: 1}
    assert any("resumed after a restart: checks" in h for h in got.history)


def test_a_job_interrupted_too_often_is_given_up_and_the_post_parked():
    store = MemoryStore()
    rec = _filed(store)
    for _ in range(MAX_ATTEMPTS):
        _crash_mid_job(store)
        Worker(store).recover()
    got = store.get(rec.post_id)
    assert got.status == PostStatus.PARKED
    assert "interrupted by restarts too many times" in (got.flags.stop_reason or "")
    assert not store.jobs.active(rec.post_id)


def test_a_post_stuck_mid_step_without_a_job_is_parked_once():
    store = MemoryStore()
    rec = PostRecord(channel_id=Channel.B, status=PostStatus.RENDERING)
    store.put(rec)
    Worker(store).recover()
    Worker(store).recover()
    got = store.get(rec.post_id)
    assert got.status == PostStatus.PARKED and got.flags.stopped_at == "video"
    assert sum("parked: interrupted by a restart" in h for h in got.history) == 1


def test_a_failing_brief_parks_its_post_and_the_worker_carries_on():
    store = MemoryStore()
    bad, good = _filed(store), PostRecord(channel_id=Channel.A)
    store.put(good)
    store.jobs.enqueue(JobKind.TOUR, good.post_id)
    calls: list[str] = []

    def boom(s, job):
        raise RuntimeError("model unreachable")

    worker = Worker(
        store, {JobKind.BRIEF: boom, JobKind.TOUR: lambda s, j: calls.append(j.post_id)}
    )
    assert worker.run_once() and worker.run_once()
    got = store.get(bad.post_id)
    assert got.status == PostStatus.PARKED and "model unreachable" in (got.flags.stop_reason or "")
    assert store.jobs.jobs_for(bad.post_id)[0].status == JobStatus.FAILED
    assert calls == [good.post_id]  # the next job still ran


def test_a_failing_final_render_is_marked_failed_with_a_retry_not_parked(tmp_path):
    from tests.releases import approved

    store = MemoryStore()
    rec = approved(tmp_path)
    rec.release.status = ReleaseStatus.PREPARING
    store.put(rec)
    store.jobs.enqueue(JobKind.RELEASE, rec.post_id)

    def boom(s, job):
        raise OSError("disk full")

    Worker(store, {JobKind.RELEASE: boom}).run_once()
    got = store.get(rec.post_id)
    assert got.status == PostStatus.APPROVED and got.release.status == ReleaseStatus.FAILED
    assert "disk full" in got.release.error and not got.approval.token


def test_a_final_render_left_preparing_by_an_older_build_is_made_again(tmp_path):
    from tests.releases import approved

    store = MemoryStore()
    rec = approved(tmp_path)
    rec.release.status = ReleaseStatus.PREPARING  # no job: the thread died with the app
    store.put(rec)
    Worker(store).recover()
    assert store.jobs.active(rec.post_id, JobKind.RELEASE)


def test_on_postgres_a_restart_resumes_the_brief(pg_url, tmp_path, monkeypatch):
    from cap.store.postgres import PostgresStore

    monkeypatch.chdir(tmp_path)
    with PostgresStore.open(pg_url) as first:
        rec = _filed(first)
        _crash_mid_job(first)  # the app stops here
    with PostgresStore.open(pg_url) as again:
        Worker(again).recover()
        drain(again, rec.post_id)
        got = again.get(rec.post_id)
        assert got.status == PostStatus.AWAITING_APPROVAL, got.history[-3:]
        assert any("resumed after a restart" in h for h in got.history)


def test_the_clock_runs_in_one_thread_only(monkeypatch):
    """Review R3: each thread ran its own clock, so scheduled work was checked twice."""
    import threading
    import time

    import cap.worker as w

    calls: list[str] = []
    monkeypatch.setattr(
        w, "schedule_due", lambda s: calls.append(threading.current_thread().name) or []
    )
    wk = w.Worker(MemoryStore(), threads=3).start()
    time.sleep(0.5)
    wk.stop()
    assert calls == ["cap-worker-0"]
