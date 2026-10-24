"""Seeding canaries (agent 16, P14, D23, D34; redesigned after review R2): a brief Hemant
files becomes the canary when one is due, reaches the queue looking like the card he
expects, and is filed again clean after his verdict; two briefs filed at once never
both become canaries."""

from __future__ import annotations

import datetime as dt
import random
import threading

import pytest

from cap.approval.app import finalise_and_issue
from cap.demo.canary_job import canary_waiting, closed_cards, file_brief, refile_clean
from cap.demo.seed import new_brief
from cap.domain.canary import (
    ClosedCard,
    PastCanary,
    cards_since_last_canary,
    draw_gap,
    next_fault,
    rate,
)
from cap.jobs import JobKind
from cap.schema.enums import FaultType
from cap.schema.post_record import Channel, PostRecord, PostStatus
from cap.store import MemoryStore
from cap.worker import drain

clean, caught, missed = ClosedCard(False), ClosedCard(True, "caught"), ClosedCard(True, "missed")


@pytest.mark.parametrize(
    ("closed", "n"),
    [(0, 3), (14, 3), (15, 5), (99, 5), (100, 10), (500, 10)],
)
def test_the_rate_is_front_loaded(closed, n):
    assert rate([clean] * closed) == n


def test_after_a_miss_one_in_three_for_ten_cards():
    cards = [clean] * 200 + [missed]
    assert rate(cards) == 3
    assert rate(cards + [clean] * 9) == 3
    assert rate(cards + [clean] * 10) == 10


def test_the_gap_is_random_around_the_rate_never_zero():
    rng = random.Random(7)
    gaps = [draw_gap(5, rng) for _ in range(2000)]
    assert min(gaps) == 1 and max(gaps) == 9 and 4.6 < sum(gaps) / len(gaps) < 5.4
    assert cards_since_last_canary([clean, caught, clean, clean]) == 2


def test_every_fault_type_comes_round_and_gate_blind_ones_sooner():
    seen, past = [], []
    for _ in range(len(FaultType)):
        f = next_fault(past)
        seen.append(f)
        past.append(PastCanary(f, gate_blind=False))
    assert sorted(seen) == sorted(FaultType)  # none skipped, none retired
    a, b = FaultType.UNSOURCED_NUMBER, FaultType.OUT_GROUP_TITLE
    past = [PastCanary(a, gate_blind=True), PastCanary(b, gate_blind=False)]
    past += [PastCanary(f, False) for f in FaultType if f not in (a, b)]
    assert next_fault(past) == a  # oldest, and the gates missed it


def close_cards(store, n):
    for k in range(n):
        p = PostRecord(channel_id=Channel.B, status=PostStatus.APPROVED)
        p.approval.decided_at = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=k)
        store.put(p)


def file(store, topic="How a heat pump moves heat"):
    rec = new_brief("A", topic)
    kind = file_brief(store, rec, {"urls": ["https://example.org/hp"], "test_log": ""},
                      idea_id="idea-1")  # fmt: skip
    return rec.post_id, kind


def test_a_brief_becomes_the_canary_once_the_gap_has_passed(canaries):
    store = MemoryStore()
    store.update_state(lambda s: setattr(s, "canary_gap", 2))
    close_cards(store, 1)
    _, kind = file(store, "How tides work")
    assert kind == JobKind.BRIEF  # one card closed, the gap is two
    close_cards(store, 1)
    post_id, kind = file(store)
    assert kind == JobKind.CANARY and store.jobs.active(post_id, JobKind.CANARY)
    rec = store.get(post_id)
    assert rec.flags.canary and rec.flags.canary_fault_type == FaultType.UNSOURCED_NUMBER
    assert rec.brief.topic == "How a heat pump moves heat"  # the topic Hemant filed
    assert rec.flags.canary_refile.urls == ["https://example.org/hp"]
    assert store.state.canary_gap == 0 and canary_waiting(store)
    assert file(store, "Why the sky is blue")[1] == JobKind.BRIEF  # one waiting: no second


def test_no_canary_while_paused(canaries):
    store = MemoryStore()
    store.update_state(lambda s: setattr(s, "canary_gap", 1))
    close_cards(store, 3)
    store.pause("a missed canary")
    assert file(store)[1] == JobKind.BRIEF


def test_two_briefs_filed_at_once_make_one_canary(canaries):
    """Review R3: the check and the act run under the store's lock."""
    store = MemoryStore()
    store.update_state(lambda s: setattr(s, "canary_gap", 1))
    close_cards(store, 3)
    kinds: list[JobKind] = []
    start = threading.Barrier(8)

    def go(i: int) -> None:
        start.wait()
        kinds.append(file(store, f"topic {i}")[1])

    threads = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert kinds.count(JobKind.CANARY) == 1


def test_two_processes_on_one_database_make_one_canary(canaries, pg_url):
    from cap.store.postgres import PostgresStore

    a, b = PostgresStore.open(pg_url), PostgresStore.open(pg_url)
    try:
        close_cards(a, 3)
        a.update_state(lambda s: setattr(s, "canary_gap", 1))  # one database
        results: list[JobKind] = []
        start = threading.Barrier(2)

        def go(store, topic):  # type: ignore[no-untyped-def]
            start.wait()
            results.append(file(store, topic)[1])

        ts = [threading.Thread(target=go, args=(s, t)) for s, t in ((a, "one"), (b, "two"))]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert results.count(JobKind.CANARY) == 1
    finally:
        a.close()
        b.close()


def test_a_canary_reaches_the_queue_like_any_card_with_the_gates_catches_kept(
    tmp_path, monkeypatch, canaries
):
    monkeypatch.chdir(tmp_path)
    store = MemoryStore()
    store.update_state(lambda s: setattr(s, "canary_gap", 1))
    close_cards(store, 1)
    post_id, kind = file(store)
    assert kind == JobKind.CANARY
    drain(store, post_id)
    rec = store.get(post_id)
    assert rec.status == PostStatus.AWAITING_APPROVAL
    assert (
        rec.flags.canary
        and rec.flags.planted_fault.span == "About 73% of people never notice this."
    )
    assert rec.flags.planted_fault.span in rec.draft.script
    assert isinstance(rec.flags.canary_gates_caught, list)
    assert any("canary gates caught" in line for line in rec.history)
    assert rec in store.queue()


def test_after_the_verdict_the_brief_is_filed_again_clean_once(tmp_path, monkeypatch, canaries):
    monkeypatch.chdir(tmp_path)
    store = MemoryStore()
    store.update_state(lambda s: setattr(s, "canary_gap", 1))
    close_cards(store, 1)
    post_id, _ = file(store)
    drain(store, post_id)
    canary = store.get(post_id)
    from cap.domain.card import record_verdict
    from cap.schema.enums import Decision, ReasonCode

    record_verdict(canary, Decision.REJECT, reviewer="hemant", time_on_card_s=600,
                   reason_code=ReasonCode.UNSOURCED_NUMBER)  # fmt: skip
    store.put(canary)
    finalise_and_issue(store, canary, "hemant")
    done = store.get(post_id)
    assert done.flags.canary_outcome == "caught" and done.flags.canary_refile is None
    clean = [p for p in store.all() if not p.flags.canary and p.brief.topic == canary.brief.topic]
    assert len(clean) == 1 and store.jobs.active(clean[0].post_id, JobKind.BRIEF)
    assert refile_clean(store, done) is None  # never twice
    assert not store.paused


def test_closed_cards_are_in_decision_order_and_skip_the_tour():
    store = MemoryStore()
    tour = PostRecord(channel_id=Channel.A, status=PostStatus.APPROVED)
    tour.flags.tour = True
    tour.approval.decided_at = dt.datetime.now(dt.UTC)
    store.put(tour)
    close_cards(store, 2)
    assert closed_cards(store) == [clean, clean]
