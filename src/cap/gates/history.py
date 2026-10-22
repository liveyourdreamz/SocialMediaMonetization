"""The post-history index the originality gate reads (PRD 4.12, R-13, D23).

A post enters the history when it is approved, never before: a draft that was rejected
or is still waiting is not a post the channel made. Canaries (D23) and the guided
tour's worked example never enter it. The gate compares against every channel's recent
posts (PRD 4.12: "queries the post-history index across all channels"); each entry
keeps its channel so a per-channel view stays possible.

Two implementations of one small interface: `InMemoryHistory` for tests and
throwaway runs, `FileHistory` (one JSON line per approved post, under the data dir) for
the app. The pipeline gets its index through PipelineDeps; nothing here is global.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from cap.schema.post_record import PostRecord, PostStatus

HISTORY_FILE = "history.jsonl"


class HistoryIndex(Protocol):
    """What the originality gate reads."""

    def recent(self, channel_id: str, days: int) -> list[tuple[str, str]]:
        """(post_id, script_text) for every post approved in the last `days` days."""
        ...


class HistoryStore(HistoryIndex, Protocol):
    """An index that can also be written, at approval."""

    def add(self, record: PostRecord) -> bool:
        """Add an approved post; False when the post may not enter the history."""
        ...


@dataclass(frozen=True)
class Entry:
    post_id: str
    channel: str
    script: str
    at: datetime


def eligible(record: PostRecord) -> bool:
    """Canaries (D23) and tour posts are never history."""
    return not record.flags.canary and not record.flags.tour


def _entry(record: PostRecord) -> Entry:
    at = record.approval.decided_at or record.created_at
    return Entry(record.post_id, record.channel_id.value, record.draft.script, at)


def _window(entries: list[Entry], days: int) -> list[tuple[str, str]]:
    cutoff = datetime.now(UTC) - timedelta(days=days)
    return [(e.post_id, e.script) for e in entries if e.at >= cutoff]


@dataclass
class InMemoryHistory:
    entries: list[Entry] = field(default_factory=list)

    def recent(self, channel_id: str, days: int) -> list[tuple[str, str]]:
        return _window(self.entries, days)

    def add(self, record: PostRecord) -> bool:
        if not eligible(record):
            return False
        if any(e.post_id == record.post_id for e in self.entries):
            return True
        self.entries.append(_entry(record))
        return True


@dataclass
class FileHistory:
    """One JSON object per line: post_id, channel, script, at (ISO 8601, UTC). Append
    only; the window is applied on read. A line that does not parse is skipped, so one
    bad write never hides the rest of the history."""

    path: Path

    def _read(self) -> list[Entry]:
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return []
        # line by line, so a write cut through a multi-byte character spoils only its
        # own line (re-verification, 22 Oct: one bad byte stopped every brief)
        lines = [b.decode("utf-8", errors="replace") for b in raw.splitlines()]
        out: list[Entry] = []
        for ln in lines:
            try:
                d = json.loads(ln)
                at = datetime.fromisoformat(d["at"])
                if at.tzinfo is None:
                    at = at.replace(tzinfo=UTC)
                out.append(Entry(str(d["post_id"]), str(d["channel"]), str(d["script"]), at))
            except (ValueError, KeyError, TypeError):
                continue
        return out

    def recent(self, channel_id: str, days: int) -> list[tuple[str, str]]:
        return _window(self._read(), days)

    def add(self, record: PostRecord) -> bool:
        if not eligible(record):
            return False
        if any(e.post_id == record.post_id for e in self._read()):
            return True  # approval is once per post; a repeat call changes nothing
        e = _entry(record)
        line = json.dumps(
            {
                "post_id": e.post_id,
                "channel": e.channel,
                "script": e.script,
                "at": e.at.isoformat(),
            },
            ensure_ascii=False,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        return True


def default_history() -> FileHistory:
    """The app's history: <data_dir>/history.jsonl."""
    from cap.config import get_settings

    return FileHistory(Path(get_settings().data_dir) / HISTORY_FILE)


def record_approved(record: PostRecord, store: HistoryStore | None = None) -> bool:
    """Call once a post is approved (the approval step's last act). Returns whether the
    post entered the history. A post that is not approved, a canary or a tour post
    never does, and neither does one drafted by the mock backend: every mock post is
    the same sample script, so letting them in would fail every later mock post on
    originality (merge review, 22 Oct)."""
    from cap.config import ModelBackend, get_settings

    if record.status not in (PostStatus.APPROVED, PostStatus.PUBLISHED):
        return False
    if get_settings().model_backend == ModelBackend.MOCK:
        return False
    return (store or default_history()).add(record)
