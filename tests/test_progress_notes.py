"""What a post's page says while it works (25 Oct: five minutes on step 2 and one log
entry, times shown four hours off)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from cap.demo.canary_job import file_brief
from cap.demo.seed import new_brief
from cap.store import MemoryStore
from cap.ui.views import log_line, waiting_note
from cap.worker import drain


def test_log_times_are_shown_in_this_computers_time_zone():
    at = datetime(2026, 10, 25, 18, 43, 39, tzinfo=UTC)
    when, what = log_line(f"{at.isoformat()} brief filed: Pyramids")
    assert when == at.astimezone().strftime("%H:%M:%S") and what == "brief filed: Pyramids"


def test_a_queued_post_says_what_it_is_waiting_for():
    store = MemoryStore()
    first, second = new_brief("B", "How tides work"), new_brief("B", "Pyramids")
    file_brief(store, first, {"urls": [], "test_log": ""})
    file_brief(store, second, {"urls": [], "test_log": ""})
    store.jobs.claim()  # the worker is on the first
    note = waiting_note(store, store.get(second.post_id))
    assert "one post at a time" in note and "How tides work" in note
    assert waiting_note(store, store.get(first.post_id)) == ""  # running: nothing to explain


def test_a_retry_says_when_and_why():
    store = MemoryStore()
    rec = new_brief("B", "Pyramids")
    file_brief(store, rec, {"urls": [], "test_log": ""})
    job = store.jobs.claim()
    store.jobs.retry(job.id, "ModelUnreachableError: P2 call 1", 60)
    note = waiting_note(store, store.get(rec.post_id))
    later = (datetime.now(UTC) + timedelta(seconds=60)).astimezone().strftime("%H:%M")
    assert note.startswith("Waiting to try this step again at") and "ModelUnreachable" in note
    assert later[:2] in note


def test_the_write_step_logs_each_part_as_it_goes():
    store = MemoryStore()
    rec = new_brief("B", "How monsoons form")
    file_brief(store, rec, {"urls": [], "test_log": ""})
    drain(store, rec.post_id)
    text = " ".join(store.get(rec.post_id).history)
    assert "script written" in text and "YouTube versions" in text
    assert "looking for a source" in text and "drawing the video" in text


def test_trend_research_never_holds_the_thread_kept_for_posts():
    from cap.jobs import JobKind
    from cap.worker import Worker

    store = MemoryStore()
    store.jobs.enqueue(JobKind.IDEAS, "ideas:B:2026-10-19")
    ran: list[str] = []
    w = Worker(store, {JobKind.IDEAS: lambda s, j: ran.append(j.kind.value)})
    assert w.run_once(posts_only=True) is False and ran == []
    assert w.run_once() is True and ran == ["ideas"]
