"""The idea book keeps the same rules in memory and in Postgres (D48)."""

from __future__ import annotations

import datetime as dt

import pytest

from cap.ideabook import IdeaBook, IdeaDecidedError, IdeaNotFoundError, MemoryIdeaBook
from cap.schema.ideas import Idea, IdeaStatus

MON = dt.date(2026, 10, 26)
PREV = MON - dt.timedelta(days=7)


@pytest.fixture(params=["memory", "postgres"])
def book(request):
    if request.param == "memory":
        yield MemoryIdeaBook()
        return
    from cap.store.postgres import PostgresStore

    store = PostgresStore.open(request.getfixturevalue("pg_url"))
    yield store.ideas
    store.close()


def idea(topic, rank=1, week=MON, channel="A", **kw):
    return Idea(channel=channel, week=week, rank=rank, topic=topic, **kw)


def test_the_digest_is_by_rank_and_held_ideas_stay_out(book: IdeaBook):
    book.add([idea("b", 2), idea("a", 1), idea("held", 0, status=IdeaStatus.HELD),
              idea("other channel", 1, channel="B")])  # fmt: skip
    assert [i.topic for i in book.week("A", MON)] == ["a", "b"]
    assert [i.topic for i in book.week("A", MON, shown_only=False)] == ["a", "b", "held"]


def test_an_idea_is_decided_once(book: IdeaBook):
    i = idea("solar")
    book.add([i])
    done = book.decide(
        i.idea_id, IdeaStatus.PICKED, topic_edit=" Why solar got cheap ", post_id="p1"
    )
    assert done.status == IdeaStatus.PICKED and done.topic_edit == "Why solar got cheap"
    assert book.get(i.idea_id).post_id == "p1" and book.get(i.idea_id).decided_at
    with pytest.raises(IdeaDecidedError):
        book.decide(i.idea_id, IdeaStatus.REJECTED)
    other = idea("x")
    book.add([other])
    with pytest.raises(ValueError):
        book.decide(other.idea_id, IdeaStatus.EXPIRED)  # decided only as picked or rejected
    with pytest.raises(IdeaNotFoundError):
        book.get("nope")


def test_unpicked_ideas_expire_and_are_remembered(book: IdeaBook):
    old, decided, now = idea("old", week=PREV), idea("decided", week=PREV), idea("now")
    book.add([old, decided, now])
    book.decide(decided.idea_id, IdeaStatus.REJECTED)
    assert book.expire(before=MON) == 1
    assert book.get(old.idea_id).status == IdeaStatus.EXPIRED
    assert book.get(decided.idea_id).status == IdeaStatus.REJECTED  # a decision is final
    assert book.get(now.idea_id).status == IdeaStatus.SHOWN
    assert {i.topic for i in book.recent("A")} == {"old", "decided", "now"}  # the memory
    assert book.week("A", PREV) and book.week("A", PREV)[0].status != IdeaStatus.SHOWN


def test_the_memory_is_ninety_days(book: IdeaBook):
    stale = idea("stale", created_at=dt.datetime.now(dt.UTC) - dt.timedelta(days=91))
    book.add([stale, idea("fresh")])
    assert [i.topic for i in book.recent("A")] == ["fresh"]
