"""Seeding canaries (agent 16, P14, D23, D34): when, which fault, on what base, and
that a canary reaches the queue looking like any card while the gates' catches are kept."""

from __future__ import annotations

import datetime as dt
import random

import pytest

from cap.demo.canary_job import (
    canary_waiting,
    closed_cards,
    pick_base,
    schedule_canary,
)
from cap.domain.canary import (
    ClosedCard,
    PastCanary,
    cards_since_last_canary,
    draw_gap,
    next_fault,
    rate,
)
from cap.ideas.week import monday_of
from cap.jobs import JobKind
from cap.schema.enums import FaultType
from cap.schema.ideas import Idea, IdeaStatus
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


def world(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = MemoryStore()
    monday = monday_of(dt.datetime.now(dt.UTC).date())
    ideas = [Idea(channel="A", week=monday, rank=r, topic=t) for r, t in
             [(1, "Why solar panels keep getting cheaper"), (2, "How a heat pump moves heat"),
              (3, "How tides work")]]  # fmt: skip
    store.ideas.add(ideas)
    return store, ideas


def close_cards(store, n):
    for k in range(n):
        p = PostRecord(channel_id=Channel.B, status=PostStatus.APPROVED)
        p.approval.decided_at = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=k)
        store.put(p)


def test_the_base_is_an_unpicked_lower_ranked_idea_on_a_fresh_topic(tmp_path, monkeypatch):
    store, ideas = world(tmp_path, monkeypatch)
    assert pick_base(store).topic == "How a heat pump moves heat"  # never rank 1
    store.put(PostRecord(channel_id=Channel.A, status=PostStatus.APPROVED,
                         brief={"topic": "How heat pumps move heat"}))  # fmt: skip
    assert pick_base(store).topic == "How tides work"  # topic posted recently: skipped


def test_seeding_waits_for_the_gap_then_takes_the_idea_out_of_the_digest(tmp_path, monkeypatch):
    store, ideas = world(tmp_path, monkeypatch)
    store.state.canary_gap = 2
    close_cards(store, 1)
    assert schedule_canary(store) is None  # one card closed, gap is two
    close_cards(store, 1)
    post_id = schedule_canary(store)
    assert post_id and store.jobs.active(post_id, JobKind.CANARY)
    rec = store.get(post_id)
    assert rec.flags.canary and rec.flags.canary_fault_type == FaultType.UNSOURCED_NUMBER
    assert store.ideas.get(ideas[1].idea_id).status == IdeaStatus.CANARY
    assert ideas[1].idea_id not in {i.idea_id for i in store.ideas.week("A", ideas[1].week)}
    assert store.state.canary_gap == 0 and canary_waiting(store)
    assert schedule_canary(store) is None  # one waiting: no second


def test_no_canary_while_paused_or_without_a_base(tmp_path, monkeypatch):
    store, _ = world(tmp_path, monkeypatch)
    store.state.canary_gap = 1
    close_cards(store, 3)
    store.pause("a missed canary")
    assert schedule_canary(store) is None
    store.reopen()
    empty = MemoryStore()
    empty.state.canary_gap = 1
    close_cards(empty, 3)
    assert schedule_canary(empty) is None  # no ideas this week: nothing to build on


def test_a_canary_reaches_the_queue_like_any_card_with_the_gates_catches_kept(
    tmp_path, monkeypatch
):
    store, _ = world(tmp_path, monkeypatch)
    store.state.canary_gap = 1
    close_cards(store, 1)
    post_id = schedule_canary(store)
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


def test_closed_cards_are_in_decision_order_and_skip_the_tour():
    store = MemoryStore()
    tour = PostRecord(channel_id=Channel.A, status=PostStatus.APPROVED)
    tour.flags.tour = True
    tour.approval.decided_at = dt.datetime.now(dt.UTC)
    store.put(tour)
    close_cards(store, 2)
    assert closed_cards(store) == [clean, clean]
