"""M1 exit: a killed worker resumes the post at the step it was on (ADR-004, ADR-006).

A real worker process takes the post's checks step and is killed outright (no clean
shutdown, as when the laptop loses power or the window is closed). A new worker on the
same database finds the step half done, runs it again, and the post reaches the
approval queue; the writing step is not done twice."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

from cap.demo.seed import new_brief
from cap.jobs import JobKind, JobStatus
from cap.schema.post_record import PostStatus
from cap.store.postgres import PostgresStore
from cap.worker import Worker, drain

_STUCK_WORKER = textwrap.dedent(
    """
    import sys, time
    from pathlib import Path
    from cap.jobs import JobKind
    from cap.store.postgres import PostgresStore
    from cap.worker import Worker

    url, marker = sys.argv[1], Path(sys.argv[2])

    def stuck(store, job):  # takes the checks step, then hangs mid-step
        marker.write_text(str(job.id))
        time.sleep(600)

    Worker(PostgresStore.open(url), {JobKind.CHECKS: stuck}).run_once()
    """
)


def test_a_killed_worker_resumes_at_the_step(pg_url, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAP_RENDERER", "text")
    from cap.config import get_settings

    get_settings.cache_clear()
    with PostgresStore.open(pg_url) as store:
        rec = new_brief("A", "A week with a note app")
        store.put(rec)
        store.jobs.enqueue(JobKind.BRIEF, rec.post_id, {"urls": [], "test_log": ""})
        assert Worker(store).run_once()  # writing finishes and queues the checks
        written = store.get(rec.post_id).draft.script

    script, marker = tmp_path / "stuck.py", tmp_path / "taken"
    script.write_text(_STUCK_WORKER, encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    proc = subprocess.Popen([sys.executable, str(script), pg_url, str(marker)], env=env)
    try:
        deadline = time.monotonic() + 60
        while not marker.exists():
            assert proc.poll() is None, "the worker process ended before taking the job"
            assert time.monotonic() < deadline, "the worker process never took the job"
            time.sleep(0.1)
        if os.name == "nt":
            proc.kill()  # TerminateProcess: no clean-up runs
        else:
            os.kill(proc.pid, signal.SIGKILL)
        proc.wait(30)
    finally:
        if proc.poll() is None:
            proc.kill()

    with PostgresStore.open(pg_url) as store:
        assert [j.status for j in store.jobs.jobs_for(rec.post_id)][-1] == JobStatus.RUNNING
        Worker(store).recover()  # the app starts again
        drain(store, rec.post_id)
        got = store.get(rec.post_id)
        assert got.status == PostStatus.AWAITING_APPROVAL, got.history[-3:]
        assert got.draft.script.endswith(written[-40:])  # the writing was not redone
        attempts = {j.kind: j.attempts for j in store.jobs.jobs_for(rec.post_id)}
        assert attempts == {JobKind.BRIEF: 1, JobKind.CHECKS: 2, JobKind.RENDER: 1}
        assert any("resumed after a restart: checks" in h for h in got.history)
    get_settings.cache_clear()
