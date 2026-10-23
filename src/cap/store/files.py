"""The laptop's JSON store: one file per post under <data_dir>/posts and a state file
for the pause and analytics. Writes are atomic (write, then rename).

Kept only until the Postgres adapter (M1 step 2b) replaces it; after that it is read
once, to import posts saved by earlier builds.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

from cap.gates.history import FileHistory, HistoryStore
from cap.schema.migrate import load_record
from cap.schema.post_record import PostRecord, PostStatus
from cap.store import MemoryStore


class FileStore(MemoryStore):
    """Posts as JSON files; see the module note."""

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = root
        posts = self.root / "posts"
        posts.mkdir(parents=True, exist_ok=True)
        self.quarantined: list[Path] = []
        # Each file on its own (review H8): one record the build cannot read is moved to
        # posts/quarantine with the reason beside it, and start-up carries on.
        for f in sorted(posts.glob("*.json")):
            try:
                loaded = load_record(f.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:  # bad JSON, bad UTF-8, failed validation
                self.quarantined.append(_quarantine(f, posts / "quarantine", e))
                continue
            rec = loaded.record
            if loaded.dropped:
                _log.info(
                    "%s: set aside fields this build does not know: %s", f.name, loaded.dropped
                )
            if rec.status in _IN_FLIGHT:
                # the thread that was working on it died with the app (review H4)
                step = _IN_FLIGHT[rec.status]
                rec.stop(step, "interrupted: the app restarted while this was running")
                rec.status = PostStatus.PARKED
                rec.log("parked: interrupted by a restart; file it again")
                _atomic_write(f, rec.model_dump_json(indent=1))
            self._posts[rec.post_id] = rec
        state = self.root / "state.json"
        if state.exists():
            try:
                d = json.loads(state.read_text(encoding="utf-8"))
                if not isinstance(d, dict):
                    raise ValueError("state.json is not a JSON object")
            except (OSError, ValueError) as e:
                self.quarantined.append(_quarantine(state, self.root / "quarantine", e))
                d = {}
            self.state.paused = bool(d.get("paused"))
            self.state.paused_reason = d.get("paused_reason", "")
            self.state.analytics = d.get("analytics", {})
            self.state.analytics_note = d.get("analytics_note", "")

    def history(self) -> HistoryStore:
        from cap.gates.history import HISTORY_FILE

        return FileHistory(self.root / HISTORY_FILE)

    def put(self, record: PostRecord) -> None:
        super().put(record)
        _atomic_write(
            self.root / "posts" / f"{record.post_id}.json", record.model_dump_json(indent=1)
        )

    def save_state(self) -> None:
        _atomic_write(
            self.root / "state.json",
            json.dumps(
                {
                    "paused": self.state.paused,
                    "paused_reason": self.state.paused_reason,
                    "analytics": self.state.analytics,
                    "analytics_note": self.state.analytics_note,
                },
                indent=1,
            ),
        )


_log = logging.getLogger("cap.store")


def _quarantine(path: Path, folder: Path, error: Exception) -> Path:
    """Move an unreadable record aside with a sidecar <name>.error.txt saying why, and
    log one line. Nothing is deleted: the file can be fixed and moved back."""
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / path.name
    if dest.exists():
        dest = folder / f"{path.stem}.{datetime.now(UTC):%Y%m%dT%H%M%S}{path.suffix}"
    os.replace(path, dest)
    dest.with_name(dest.name + ".error.txt").write_text(
        f"{datetime.now(UTC).isoformat()} moved here from {path.parent.name}/ at start-up:\n"
        f"this build could not read it ({type(error).__name__}).\n\n{error}\n",
        encoding="utf-8",
    )
    _log.warning("could not read %s; moved it to %s (reason beside it)", path.name, dest)
    return dest


_IN_FLIGHT = {
    PostStatus.BRIEFED: "draft",
    PostStatus.DRAFTING: "draft",
    PostStatus.GATED: "checks",
    PostStatus.RENDERING: "video",
}


_WRITE_LOCKS: dict[Path, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _atomic_write(path: Path, text: str) -> None:
    """Write, then rename. One lock per file: two threads saving the same post take
    turns (on Windows a rename onto a file another rename is replacing is refused,
    review M6), and each uses its own temp name."""
    import time
    import uuid

    with _LOCKS_GUARD:
        lock = _WRITE_LOCKS.setdefault(path.resolve(), threading.Lock())
    with lock:
        tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")
        tmp.write_text(text, encoding="utf-8")
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:  # Windows: a reader (or antivirus) holds it briefly
                if attempt == 4:
                    tmp.unlink(missing_ok=True)
                    raise
                time.sleep(0.05 * (attempt + 1))
