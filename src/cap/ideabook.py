"""The idea book: where the week's ideas are kept, decided and remembered (D48).

A port with two adapters, like the job queue: in memory (tests, the offline check)
and Postgres (cap.store.pgideas). Rules every adapter keeps (tests/test_ideabook.py
runs them against both):

* an idea is decided once: shown -> picked or rejected; a decided idea is final;
* `expire` turns every idea still shown from an earlier week into expired;
* `recent` is the memory: every idea of a channel from the last N days, whatever its
  status, newest first, so trend research never proposes the same topic again.
"""

from __future__ import annotations

import datetime as dt
import threading
from abc import ABC, abstractmethod

from cap.schema.ideas import DECIDED, HIDDEN, Idea, IdeaStatus

MEMORY_DAYS = 90


class IdeaNotFoundError(KeyError):
    pass


class IdeaDecidedError(ValueError):
    """The idea was already picked or rejected, or is not in the Digest."""


def _decide(idea: Idea, status: IdeaStatus, topic_edit: str, post_id: str | None) -> Idea:
    if status not in DECIDED:
        raise ValueError(f"an idea is decided as picked or rejected, not {status.value}")
    if idea.status != IdeaStatus.SHOWN:
        raise IdeaDecidedError(f"idea {idea.idea_id} is {idea.status.value}")
    return idea.model_copy(
        update={
            "status": status,
            "topic_edit": topic_edit.strip()[:140],
            "post_id": post_id,
            "decided_at": dt.datetime.now(dt.UTC),
        }
    )


class IdeaBook(ABC):
    @abstractmethod
    def add(self, ideas: list[Idea]) -> None: ...

    @abstractmethod
    def get(self, idea_id: str) -> Idea: ...

    @abstractmethod
    def week(self, channel: str, monday: dt.date, shown_only: bool = True) -> list[Idea]:
        """A channel's ideas for one Digest, by rank (held ones last when included)."""

    @abstractmethod
    def decide(
        self, idea_id: str, status: IdeaStatus, topic_edit: str = "", post_id: str | None = None
    ) -> Idea: ...

    @abstractmethod
    def expire(self, before: dt.date) -> int:
        """Ideas still shown from a week before `before` become expired; returns how many."""

    @abstractmethod
    def recent(self, channel: str, days: int = MEMORY_DAYS) -> list[Idea]: ...

    def remembered(self, channel: str, days: int = MEMORY_DAYS) -> list[Idea]:
        """The ideas that keep a topic from being proposed again: ones Hemant saw or
        decided. A held idea he never saw blocks nothing (review R11)."""
        return [i for i in self.recent(channel, days) if i.status not in HIDDEN]


class MemoryIdeaBook(IdeaBook):
    def __init__(self) -> None:
        self._ideas: dict[str, Idea] = {}
        self._lock = threading.Lock()

    def add(self, ideas: list[Idea]) -> None:
        with self._lock:
            for i in ideas:
                self._ideas[i.idea_id] = i.model_copy(deep=True)

    def get(self, idea_id: str) -> Idea:
        with self._lock:
            if idea_id not in self._ideas:
                raise IdeaNotFoundError(idea_id)
            return self._ideas[idea_id].model_copy(deep=True)

    def week(self, channel: str, monday: dt.date, shown_only: bool = True) -> list[Idea]:
        with self._lock:
            found = [
                i.model_copy(deep=True)
                for i in self._ideas.values()
                if i.channel == channel
                and i.week == monday
                and (not shown_only or i.status not in HIDDEN)
            ]
        return sorted(found, key=lambda i: (i.rank == 0, i.rank))

    def decide(
        self, idea_id: str, status: IdeaStatus, topic_edit: str = "", post_id: str | None = None
    ) -> Idea:
        with self._lock:
            if idea_id not in self._ideas:
                raise IdeaNotFoundError(idea_id)
            done = _decide(self._ideas[idea_id], status, topic_edit, post_id)
            self._ideas[idea_id] = done
            return done.model_copy(deep=True)

    def expire(self, before: dt.date) -> int:
        n = 0
        with self._lock:
            for k, i in self._ideas.items():
                if i.status == IdeaStatus.SHOWN and i.week < before:
                    self._ideas[k] = i.model_copy(update={"status": IdeaStatus.EXPIRED})
                    n += 1
        return n

    def recent(self, channel: str, days: int = MEMORY_DAYS) -> list[Idea]:
        since = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)
        with self._lock:
            found = [
                i.model_copy(deep=True)
                for i in self._ideas.values()
                if i.channel == channel and i.created_at >= since
            ]
        return sorted(found, key=lambda i: i.created_at, reverse=True)
