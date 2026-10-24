"""Bring the laptop's JSON records into Postgres, once (M1 step 2).

Runs at start-up when the database has no posts and <data_dir>/posts exists. Every
post, the pause and analytics state, and the originality history are copied; then the
old files are moved aside to <data_dir>/imported-<UTC time>/ (never deleted), so a
second start does not import them again and they can be inspected or restored.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from cap.gates.history import HISTORY_FILE, Entry, FileHistory
from cap.schema.post_record import Channel, PostRecord, PostStatus
from cap.store import AppState, PostNotFoundError, PostStore
from cap.store.files import FileStore

_MOVED = ("posts", "state.json", HISTORY_FILE, "quarantine")


def needed(data_dir: Path, store: PostStore) -> bool:
    return (data_dir / "posts").is_dir() and store.count() == 0


def import_files(data_dir: Path, store: PostStore) -> int:
    """Copy the JSON records into `store`; return how many posts came across."""
    old = FileStore(data_dir)  # parks interrupted posts, quarantines broken files
    for rec in old.all():
        rec.revision = 0  # new to the database, whatever the file store counted
        store.put(rec)
    kept = old.state

    def change(s: AppState) -> None:
        s.paused, s.paused_reason = kept.paused, kept.paused_reason
        s.analytics, s.analytics_note = kept.analytics, kept.analytics_note

    store.update_state(change)
    history = store.history()
    for entry in FileHistory(data_dir / HISTORY_FILE).entries():
        history.add(_history_record(old, entry))
    _move_aside(data_dir)
    return old.count()


def _history_record(old: FileStore, entry: Entry) -> PostRecord:
    """The post itself when its file survives; otherwise the history line alone, which
    still counts for originality."""
    try:
        return old.get(entry.post_id)
    except PostNotFoundError:
        rec = PostRecord(
            channel_id=Channel(entry.channel), status=PostStatus.APPROVED, post_id=entry.post_id
        )
        rec.draft.script = entry.script
        rec.approval.decided_at = entry.at
        return rec


def _move_aside(data_dir: Path) -> None:
    dest = data_dir / f"imported-{datetime.now(UTC):%Y%m%dT%H%M%S}"
    dest.mkdir()
    for name in _MOVED:
        src = data_dir / name
        if src.exists():
            os.replace(src, dest / name)
    (dest / "README.txt").write_text(
        f"Copied into the app's Postgres database on {datetime.now(UTC):%d %b %Y %H:%M} UTC.\n"
        "Kept only for reference; the app no longer reads these files.\n",
        encoding="utf-8",
    )
