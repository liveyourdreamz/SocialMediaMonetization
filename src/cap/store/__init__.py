"""The post store: the one port every layer uses to keep and find posts (M1, ADR-006).

`PostStore` is the contract. Two adapters implement it today:

* `MemoryStore`: in memory, for tests and the offline check.
* `cap.store.files.FileStore`: the laptop's JSON files. It is kept only until the
  Postgres adapter replaces it (M1 step 2b) and then only to import old posts once.

The store keeps facts, never wiring: no adapter, client or setting lives on it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cap.schema.post_record import PostRecord, PostStatus

if TYPE_CHECKING:
    from cap.gates.history import HistoryStore


class PostNotFoundError(LookupError):
    """No post has this id. The web layer turns it into a 404."""


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

    def __init__(self) -> None:
        self.state = AppState()

    # ---- posts ------------------------------------------------------------------------

    @abstractmethod
    def get(self, post_id: str) -> PostRecord:
        """The post, or PostNotFoundError."""

    @abstractmethod
    def put(self, record: PostRecord) -> None:
        """Save the post as it is now (insert or replace)."""

    @abstractmethod
    def all(self) -> list[PostRecord]:
        """Every post, in no particular order."""

    def count(self) -> int:
        return len(self.all())

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
        super().__init__()
        self._posts: dict[str, PostRecord] = {}
        self._opened: dict[tuple[str, str], datetime] = {}

    def get(self, post_id: str) -> PostRecord:
        try:
            return self._posts[post_id]
        except KeyError as e:
            raise PostNotFoundError(post_id) from e

    def put(self, record: PostRecord) -> None:
        self._posts[record.post_id] = record

    def all(self) -> list[PostRecord]:
        return list(self._posts.values())

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
