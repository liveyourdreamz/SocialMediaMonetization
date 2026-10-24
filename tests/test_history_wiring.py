"""Review R1 (24 Oct): the originality gate must read the history approvals are written
to. The app built its pipeline without one and fell back to a file, while approvals
went to Postgres, so every draft was compared against nothing."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cap.demo.seed import _setup
from cap.schema.post_record import Brief, Channel, PostRecord, PostStatus
from cap.store import MemoryStore
from cap.store.postgres import PostgresStore


def _approved(script: str) -> PostRecord:
    r = PostRecord(channel_id=Channel.A, status=PostStatus.APPROVED)
    r.draft.script = script
    r.approval.decided_at = datetime.now(UTC)
    return r


@pytest.fixture(params=["memory", "postgres"])
def store(request):
    if request.param == "memory":
        yield MemoryStore()
        return
    s = PostgresStore.open(request.getfixturevalue("pg_url"))
    try:
        yield s
    finally:
        s.close()


def test_the_pipeline_reads_the_history_the_store_writes(store):
    post = _approved("how a heat pump moves heat uphill")
    assert store.history().add(post)
    brief = PostRecord(channel_id=Channel.A, brief=Brief(topic="heat pumps"))
    deps, _ = _setup(store, brief, "")
    assert [pid for pid, _ in deps.history.recent("A", 90)] == [post.post_id]


def test_a_memory_store_keeps_its_own_history():
    """Two in-memory stores never share approvals (the file fallback did)."""
    a, b = MemoryStore(), MemoryStore()
    assert a.history().add(_approved("one script"))
    assert b.history().recent("A", 90) == []
