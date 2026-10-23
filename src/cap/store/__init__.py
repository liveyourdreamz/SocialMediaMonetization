"""The post store: the one port every layer uses to keep and find posts (M1, ADR-006).

`PostStore` is the contract. Two adapters implement it today:

* `MemoryStore`: in memory, for tests and the offline check.
* `cap.store.files.FileStore`: the laptop's JSON files. It is kept only until the
  Postgres adapter replaces it (M1 step 2b) and then only to import old posts once.

The store keeps facts, never wiring: no adapter, client or setting lives on it. Its
job queue (`store.jobs`, cap.jobs) lives beside the posts in the same database.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cap.jobs import JobQueue, MemoryJobQueue
from cap.schema.post_record import PostRecord, PostStatus

if TYPE_CHECKING:
    from cap.gates.history import HistoryStore


_UPDATE_TRIES = 20

# Statuses a post is in only while a worker is busy with it, and the step each means.
IN_FLIGHT = {
    PostStatus.BRIEFED: "draft",
    PostStatus.DRAFTING: "draft",
    PostStatus.GATED: "checks",
    PostStatus.RENDERING: "video",
}


def park_if_interrupted(record: PostRecord) -> bool:
    """A post found mid-step at start-up lost its worker when the app stopped (review
    H4) and has no job to resume it (cap.worker.recover). Park it with the step it was
    on; True when the record changed."""
    step = IN_FLIGHT.get(record.status)
    if step is None:
        return False
    record.stop(step, "interrupted: the app restarted while this was running")
    record.status = PostStatus.PARKED
    record.log("parked: interrupted by a restart; file it again")
    return True


class PostNotFoundError(LookupError):
    """No post has this id. The web layer turns it into a 404."""


class PostConflictError(RuntimeError):
    """The post was saved by someone else since this copy was read. Re-read and redo
    the change; never overwrite (the other save would be lost)."""


@dataclass
class AppState:
    """What the app remembers besides posts: the canary pause (P14) and agent 14's
    figures, with a note when they are example figures (demo mode)."""

    paused: bool = False
    paused_reason: str = ""
    analytics: dict[str, Any] = field(default_factory=dict)
    analytics_note: str = ""


class PostStore(ABC):
    """Keep posts, the app state and the card timers.

    Time on card (D34) is the server's clock, never the browser's: the first open of a
    card by a reviewer starts it.
    """

    def __init__(self, jobs: JobQueue) -> None:
        self.jobs = jobs
        self.state = AppState()
        self.unreadable: list[str] = []  # stored posts this build could not read, by id

    # ---- posts ------------------------------------------------------------------------

    @abstractmethod
    def get(self, post_id: str) -> PostRecord:
        """The post, or PostNotFoundError."""

    @abstractmethod
    def put(self, record: PostRecord) -> None:
        """Save the post. `record.revision` must equal the stored revision (0 for a new
        post), else PostConflictError; on success the store and `record` both move to
        the next revision."""

    @abstractmethod
    def all(self) -> list[PostRecord]:
        """Every post, in no particular order."""

    def count(self) -> int:
        return len(self.all())

    def update(self, post_id: str, change: Callable[[PostRecord], object]) -> PostRecord:
        """Read the post, apply `change`, save; on a conflict read again and re-apply.
        The safe way for background work to touch a post someone else may be saving."""
        for _ in range(_UPDATE_TRIES):
            record = self.get(post_id)
            change(record)
            try:
                self.put(record)
                return record
            except PostConflictError:
                continue
        raise PostConflictError(post_id)

    def queue(self) -> list[PostRecord]:
        """Posts awaiting approval, oldest first (the only priority)."""
        waiting = [p for p in self.all() if p.status == PostStatus.AWAITING_APPROVAL]
        return sorted(waiting, key=lambda p: p.created_at)

    # ---- card timers (D34) ------------------------------------------------------------

    @abstractmethod
    def _card_opened_at(self, post_id: str, reviewer: str) -> datetime | None: ...

    @abstractmethod
    def _set_card_opened_at(self, post_id: str, reviewer: str, at: datetime) -> None: ...

    def open_card(self, post_id: str, reviewer: str) -> int:
        """The first open starts the clock; later opens return the seconds so far."""
        at = self._card_opened_at(post_id, reviewer)
        now = datetime.now(UTC)
        if at is None:
            self._set_card_opened_at(post_id, reviewer, now)
            return 0
        return int((now - at).total_seconds())

    def seconds_on_card(self, post_id: str, reviewer: str) -> int:
        at = self._card_opened_at(post_id, reviewer)
        return int((datetime.now(UTC) - at).total_seconds()) if at else 0

    # ---- app state ---------------------------------------------------------------------

    @property
    def paused(self) -> bool:
        return self.state.paused

    @property
    def paused_reason(self) -> str:
        return self.state.paused_reason

    def pause(self, reason: str) -> None:
        self.state.paused, self.state.paused_reason = True, reason
        self.save_state()

    def reopen(self) -> None:
        self.state.paused, self.state.paused_reason = False, ""
        self.save_state()

    def set_analytics(self, figures: dict[str, Any], note: str = "") -> None:
        self.state.analytics, self.state.analytics_note = figures, note
        self.save_state()

    @abstractmethod
    def save_state(self) -> None:
        """Persist `self.state`."""

    # ---- where related records live ---------------------------------------------------

    @abstractmethod
    def history(self) -> HistoryStore:
        """Where approved posts are kept for the originality gate."""

    def local_published_dir(self) -> Path:
        """Where the local record of every publish goes (<data_dir>/published)."""
        from cap.config import get_settings

        return Path(get_settings().data_dir) / "published"


class MemoryStore(PostStore):
    """Everything in memory; nothing survives the process. Tests and the offline check."""

    def __init__(self) -> None:
        super().__init__(MemoryJobQueue())
        self._posts: dict[str, PostRecord] = {}
        self._lock = threading.Lock()
        self._opened: dict[tuple[str, str], datetime] = {}

    # Copies in and out, as a database does: code that changes a record it read must
    # save it, and nothing may rely on two readers sharing one object.
    def get(self, post_id: str) -> PostRecord:
        try:
            return self._posts[post_id].model_copy(deep=True)
        except KeyError as e:
            raise PostNotFoundError(post_id) from e

    def put(self, record: PostRecord) -> None:
        with self._lock:
            stored = self._posts.get(record.post_id)
            if (stored.revision if stored else 0) != record.revision:
                raise PostConflictError(record.post_id)
            record.revision += 1
            self._posts[record.post_id] = record.model_copy(deep=True)

    def all(self) -> list[PostRecord]:
        return [p.model_copy(deep=True) for p in self._posts.values()]

    def count(self) -> int:
        return len(self._posts)

    def _card_opened_at(self, post_id: str, reviewer: str) -> datetime | None:
        return self._opened.get((post_id, reviewer))

    def _set_card_opened_at(self, post_id: str, reviewer: str, at: datetime) -> None:
        self._opened[(post_id, reviewer)] = at

    def save_state(self) -> None:
        return None

    def history(self) -> HistoryStore:
        from cap.gates.history import default_history

        return default_history()
