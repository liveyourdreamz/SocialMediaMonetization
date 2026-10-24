"""The laptop's JSON records come into Postgres once, and nothing is deleted (M1 step 2)."""

from __future__ import annotations

import json

from cap.config import ModelBackend, get_settings
from cap.gates.history import FileHistory
from cap.schema.post_record import Channel, PostRecord, PostStatus
from cap.store import importer
from cap.store.files import FileStore
from cap.store.postgres import PostgresStore


def _old_laptop(data) -> tuple[PostRecord, PostRecord]:
    files = FileStore(data)
    waiting = PostRecord(channel_id=Channel.A, status=PostStatus.AWAITING_APPROVAL)
    approved = PostRecord(channel_id=Channel.B, status=PostStatus.APPROVED)
    approved.draft.script = "From orbit, silk looks like nothing at all."
    for r in (waiting, approved):
        files.put(r)
    files.pause("a canary was missed")
    FileHistory(data / "history.jsonl").add(approved)
    # a history line whose post file is gone still counts
    gone = PostRecord(channel_id=Channel.C, status=PostStatus.APPROVED)
    gone.draft.script = "نص قديم"
    FileHistory(data / "history.jsonl").add(gone)
    return waiting, approved


def test_old_records_move_into_postgres_once(pg_url, tmp_path, monkeypatch):
    monkeypatch.setenv("CAP_MODEL_BACKEND", ModelBackend.ANTHROPIC.value)
    get_settings.cache_clear()
    data = tmp_path / "cap-data"
    waiting, approved = _old_laptop(data)
    with PostgresStore.open(pg_url) as store:
        assert importer.needed(data, store)
        assert importer.import_files(data, store) == 2
        assert {p.post_id for p in store.all()} == {waiting.post_id, approved.post_id}
        assert store.paused and store.paused_reason == "a canary was missed"
        scripts = [s for _, s in store.history().recent("A", 90)]
        assert approved.draft.script in scripts and "نص قديم" in scripts
        assert not importer.needed(data, store)  # a second start does not import again
    moved = next(data.glob("imported-*"))
    assert (moved / "posts" / f"{waiting.post_id}.json").exists()  # kept, not deleted
    assert json.loads((moved / "state.json").read_text(encoding="utf-8"))["paused"] is True
    assert not (data / "posts").exists()
    get_settings.cache_clear()


def test_a_fresh_laptop_has_nothing_to_import(pg_url, tmp_path):
    with PostgresStore.open(pg_url) as store:
        assert not importer.needed(tmp_path / "cap-data", store)
