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
