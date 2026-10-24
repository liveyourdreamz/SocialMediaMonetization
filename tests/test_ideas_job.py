"""The weekly run as a job, and the clock that queues it (D48, ADR-017)."""

from __future__ import annotations

import datetime as dt

from cap.demo.ideas_job import due, job_key, parse_key, run_ideas_job, schedule_due
from cap.ideas.week import Sources
from cap.jobs import Job, JobKind
from cap.models import MockClient
from cap.schema.ideas import Idea
from cap.store import MemoryStore
from cap.worker import Worker
from tests.test_ideas_week import STORY, client, judged, p4_item

MON = dt.date(2026, 10, 26)
UTC = dt.UTC


def at(day: int, hour: int) -> dt.datetime:  # a time in UTC; Dubai is UTC+4
    return dt.datetime(2026, 10, day, hour, 0, tzinfo=UTC)


def test_the_run_is_due_from_monday_eight_in_dubai():
    assert due(at(26, 3)) is None  # 07:00 in Dubai on Monday
    assert due(at(26, 4)) == MON  # 08:00 in Dubai
    assert due(at(29, 12)) == MON  # later in the week: still this week's run
    assert due(at(25, 23)) is None  # Sunday 23:00 UTC is Monday 03:00 in Dubai: too early


def test_the_clock_queues_each_channel_once_a_week():
    store = MemoryStore()
    keys = schedule_due(store, at(26, 5))
    assert keys == [job_key(ch, MON) for ch in ("A", "B", "C")]
    assert schedule_due(store, at(26, 6)) == []  # queued already
    assert parse_key(keys[0]) == ("A", MON)


def test_a_week_that_has_ideas_is_not_run_again():
    store = MemoryStore()
    store.ideas.add([Idea(channel="A", week=MON, rank=1, topic="How tides work")])
    assert job_key("A", MON) not in schedule_due(store, at(26, 5))


def fake_sources():
    return Sources(
        demand=lambda ch, today: [],
        feeds=lambda ch, today: ([STORY], []),
        wiki_search=lambda q, lang: [],
        fetch_page=lambda url: "Module prices fell.",
    )


def test_the_job_runs_the_week_and_reports_to_the_audit_log():
    store = MemoryStore()
    c = client([p4_item("Why solar panels keep getting cheaper", demand_ref=None)], [judged()])
    run_ideas_job(store, Job(1, JobKind.IDEAS, job_key("A", MON)), fake_sources(), c)
    assert [i.topic for i in store.ideas.week("A", MON)] == [
        "Why solar panels keep getting cheaper"
    ]
    entry = [e for e in store.audit.entries() if e.action == "ideas run"][-1]
    assert "1 ideas, 0 held" in entry.detail["report"]


def test_a_failed_run_is_recorded_without_a_post():
    store = MemoryStore()

    def boom(store, job):
        raise RuntimeError("feeds down")

    store.jobs.enqueue(JobKind.IDEAS, job_key("B", MON))
    Worker(store, handlers={JobKind.IDEAS: boom}).run_once()
    failed = [e for e in store.audit.entries() if e.action == "ideas run failed"]
    assert failed and "feeds down" in failed[0].detail["why"]


def test_mock_backend_without_a_script_stops_visibly_not_silently():
    store = MemoryStore()
    run_ideas_job(store, Job(1, JobKind.IDEAS, job_key("C", MON)), fake_sources(), MockClient())
    report = [e for e in store.audit.entries() if e.action == "ideas run"][-1].detail["report"]
    assert "stopped: trend research" in report
    assert store.ideas.week("C", MON, shown_only=False) == []
