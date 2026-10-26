"""The job queue keeps the same rules in memory and in Postgres (M1 step 3, ADR-006)."""

from __future__ import annotations

import threading

import pytest

from cap.jobs import MAX_ATTEMPTS, JobKind, JobQueue, JobStatus, MemoryJobQueue


@pytest.fixture(params=["memory", "postgres"])
def queue(request):
    if request.param == "memory":
        yield MemoryJobQueue()
        return
    from cap.store.postgres import PostgresStore

    store = PostgresStore.open(request.getfixturevalue("pg_url"))
    yield store.jobs
    store.close()


def test_the_same_work_is_queued_once(queue: JobQueue):
    a = queue.enqueue(JobKind.BRIEF, "p1", {"topic": "silk"})
    assert queue.enqueue(JobKind.BRIEF, "p1", {"topic": "again"}) == a
    assert queue.enqueue(JobKind.RELEASE, "p1") != a  # other work for the post is separate
    job = queue.claim()
    assert job and job.id == a and job.payload == {"topic": "silk"} and job.attempts == 1
    assert queue.enqueue(JobKind.BRIEF, "p1") == a  # still running: still the same job
    queue.finish(a)
    assert queue.enqueue(JobKind.BRIEF, "p1") != a  # done: new work is a new job


def test_oldest_first_and_one_running_job_per_post(queue: JobQueue):
    first = queue.enqueue(JobKind.BRIEF, "p1")
    second = queue.enqueue(JobKind.RELEASE, "p1")
    other = queue.enqueue(JobKind.BRIEF, "p2")
    assert queue.claim().id == first
    assert queue.claim().id == other  # p1 is busy, so p2 goes next
    assert queue.claim() is None
    queue.finish(first)
    assert queue.claim().id == second


def test_active_follows_the_job(queue: JobQueue):
    j = queue.enqueue(JobKind.RELEASE, "p1")
    assert queue.active("p1") and queue.active("p1", JobKind.RELEASE)
    assert not queue.active("p1", JobKind.BRIEF) and not queue.active("p2")
    queue.claim()
    queue.fail(j, "edge-tts timed out")
    assert not queue.active("p1")
    assert queue.jobs_for("p1")[0].status == JobStatus.FAILED
    assert "edge-tts" in queue.jobs_for("p1")[0].error
    assert queue.wait("p1", timeout=1)


def test_a_restart_resumes_running_jobs_until_they_have_failed_too_often(queue: JobQueue):
    j = queue.enqueue(JobKind.BRIEF, "p1")
    for attempt in range(1, MAX_ATTEMPTS):
        assert queue.claim().attempts == attempt
        resumed, given_up = queue.recover()  # the app stopped mid-job
        assert [x.id for x in resumed] == [j] and not given_up
    assert queue.claim().attempts == MAX_ATTEMPTS
    resumed, given_up = queue.recover()
    assert not resumed and [x.id for x in given_up] == [j]
    assert not queue.active("p1")


def test_concurrent_workers_never_take_the_same_job(queue: JobQueue):
    ids = {queue.enqueue(JobKind.BRIEF, f"p{i}") for i in range(30)}
    taken: list[int] = []
    lock = threading.Lock()

    def work() -> None:
        while (job := queue.claim()) is not None:
            with lock:
                taken.append(job.id)
            queue.finish(job.id)

    threads = [threading.Thread(target=work) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(taken) == sorted(ids)  # every job once, none twice


# ---- review R6: a live job is never taken; a dead worker's job comes back ---------------


def test_a_job_a_live_worker_holds_is_never_taken_back(pg_url):
    import threading

    from cap.store.postgres import PostgresStore

    a, b = PostgresStore.open(pg_url), PostgresStore.open(pg_url)
    try:
        a.jobs.enqueue(JobKind.BRIEF, "p1")
        inside, release = threading.Event(), threading.Event()

        def run() -> None:
            with a.jobs.held() as job:
                assert job is not None
                inside.set()
                release.wait(10)
                a.jobs.finish(job.id)

        t = threading.Thread(target=run)
        t.start()
        assert inside.wait(10)
        assert b.jobs.recover() == ([], [])  # another process starting up
        assert b.jobs.claim() is None  # and nothing to run twice
        release.set()
        t.join()
        assert [j.status for j in b.jobs.jobs_for("p1")] == [JobStatus.DONE]
    finally:
        a.close()
        b.close()


def test_a_dead_workers_job_comes_back_without_a_restart(pg_url):
    import psycopg

    from cap.store.pgjobs import _CLAIM, JOB_LOCKS
    from cap.store.postgres import PostgresStore

    store = PostgresStore.open(pg_url)
    try:
        store.jobs.enqueue(JobKind.BRIEF, "p1")
        dead = psycopg.connect(pg_url, autocommit=True)  # a worker that then dies
        row = dead.execute(_CLAIM, ([],)).fetchone()
        dead.execute("SELECT pg_advisory_lock(%s, %s)", (JOB_LOCKS, row[0]))
        assert store.jobs.recover() == ([], [])  # alive: left alone
        dead.close()  # the process dies; its lock goes with it
        resumed, _ = store.jobs.recover()
        assert [j.id for j in resumed] == [row[0]]
    finally:
        store.close()


def test_saving_a_post_and_queueing_its_job_is_all_or_nothing(pg_url, monkeypatch):
    import pytest

    import cap.store.postgres as pg
    from cap.demo.seed import new_brief
    from cap.store import PostNotFoundError

    store = pg.PostgresStore.open(pg_url)
    try:
        rec = new_brief("A", "How tides work")

        def broken(*a, **k):  # type: ignore[no-untyped-def]
            raise RuntimeError("the queue insert failed")

        monkeypatch.setattr(pg, "insert_job", broken)
        with pytest.raises(RuntimeError):
            store.file(rec, JobKind.BRIEF, {})
        with pytest.raises(PostNotFoundError):
            store.get(rec.post_id)  # no post without its job
    finally:
        store.close()
