"""The chained audit log (M1 step 3c, ADR-011, D45).

Append-only: every entry stores the hash of the entry before it, so editing, deleting
or inserting a row anywhere breaks the chain, and `verify` (run at start-up) says
where. In Postgres, triggers also refuse UPDATE and DELETE outright; the only deletion
is the retention purge (settings.audit_retention_days, 90 until counsel confirms),
which records the last purged hash as the chain's new anchor.

What is written: every save of a post (who, the new revision, the status and the
history lines it added; every decision on the card lands in those lines), and the
events that are not post saves: sign-ins, the queue pause, platform connections.
Who: the signed-in person for a request, "worker" for queued jobs, "system" otherwise
(set per request or job through `acting_as`).
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import threading
from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

GENESIS = ""  # the prev_hash of the very first entry

_ACTOR: contextvars.ContextVar[str] = contextvars.ContextVar("cap_audit_actor", default="system")


@contextmanager
def acting_as(actor: str) -> Iterator[None]:
    """Entries written inside this block are attributed to `actor`."""
    token = _ACTOR.set(actor or "system")
    try:
        yield
    finally:
        _ACTOR.reset(token)


def current_actor() -> str:
    return _ACTOR.get()


@dataclass(frozen=True)
class Entry:
    id: int
    at: datetime
    actor: str
    action: str
    post_id: str
    detail: dict[str, Any] = field(default_factory=dict)
    prev_hash: str = GENESIS
    hash: str = ""


def entry_hash(prev_hash: str, id_: int, at: datetime, actor: str, action: str,
               post_id: str, detail: dict[str, Any]) -> str:  # fmt: skip
    body = json.dumps(
        [id_, at.astimezone(UTC).isoformat(), actor, action, post_id, detail],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(f"{prev_hash}\n{body}".encode()).hexdigest()


@dataclass(frozen=True)
class ChainCheck:
    ok: bool
    entries: int
    broken_at: int | None = None  # the first entry id where the chain does not hold
    why: str = ""


def verify_chain(entries: list[Entry], anchor_hash: str, first_id: int) -> ChainCheck:
    """Walk the entries in id order from the anchor: ids must follow on without a gap
    and every hash must recompute."""
    prev, expected_id = anchor_hash, first_id
    for e in entries:
        if e.id != expected_id:
            return ChainCheck(False, len(entries), expected_id, f"entry {expected_id} is missing")
        if e.prev_hash != prev:
            return ChainCheck(False, len(entries), e.id, f"entry {e.id} does not follow on")
        if entry_hash(e.prev_hash, e.id, e.at, e.actor, e.action, e.post_id, e.detail) != e.hash:
            return ChainCheck(False, len(entries), e.id, f"entry {e.id} was changed")
        prev, expected_id = e.hash, e.id + 1
    return ChainCheck(True, len(entries))


class AuditLog(ABC):
    @abstractmethod
    def append(self, action: str, post_id: str = "", detail: dict[str, Any] | None = None) -> Entry:
        """Write one entry, attributed to the current actor."""

    @abstractmethod
    def entries(self, post_id: str | None = None) -> list[Entry]:
        """Entries in order (one post's, or all)."""

    @abstractmethod
    def verify(self) -> ChainCheck: ...

    @abstractmethod
    def purge(self, older_than_days: int) -> int:
        """Delete entries older than the retention period; the chain is re-anchored on
        the last one deleted. Returns how many went."""


class MemoryAuditLog(AuditLog):
    def __init__(self) -> None:
        self._rows: list[Entry] = []
        self._anchor = (GENESIS, 1)  # (hash before the first kept entry, its id)
        self._lock = threading.Lock()

    def append(self, action: str, post_id: str = "", detail: dict[str, Any] | None = None) -> Entry:
        with self._lock:
            prev = self._rows[-1].hash if self._rows else self._anchor[0]
            id_ = self._rows[-1].id + 1 if self._rows else self._anchor[1]
            at, actor, d = datetime.now(UTC), current_actor(), dict(detail or {})
            e = Entry(id_, at, actor, action, post_id, d, prev,
                      entry_hash(prev, id_, at, actor, action, post_id, d))  # fmt: skip
            self._rows.append(e)
            return e

    def entries(self, post_id: str | None = None) -> list[Entry]:
        with self._lock:
            return [e for e in self._rows if post_id is None or e.post_id == post_id]

    def verify(self) -> ChainCheck:
        with self._lock:
            return verify_chain(list(self._rows), *self._anchor)

    def purge(self, older_than_days: int) -> int:
        cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
        with self._lock:
            old = [e for e in self._rows if e.at < cutoff]
            if old:
                self._anchor = (old[-1].hash, old[-1].id + 1)
                self._rows = self._rows[len(old) :]
            return len(old)
