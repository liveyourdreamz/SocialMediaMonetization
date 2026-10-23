"""The chained audit log (M1 step 3c, ADR-011): every save and sign-in is written, the
table refuses edits, and any change made behind the app's back is found at start-up."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from cap.audit import MemoryAuditLog, acting_as
from cap.schema.post_record import Channel, PostRecord
from cap.store import MemoryStore
from tests.test_ui import world  # noqa: F401 - the fixture


@pytest.fixture(params=["memory", "postgres"])
def store(request):
    if request.param == "memory":
        yield MemoryStore()
        return
    from cap.store.postgres import PostgresStore

    s = PostgresStore.open(request.getfixturevalue("pg_url"))
    yield s
    s.close()


def test_every_save_is_written_with_who_and_what_changed(store):
    rec = PostRecord(channel_id=Channel.A)
    rec.log("brief filed: silk")
    with acting_as("hemant"):
        store.put(rec)
        rec.log("verdict: approve")
        store.put(rec)
    entries = store.audit.entries(rec.post_id)
    assert [e.actor for e in entries] == ["hemant", "hemant"]
    assert [e.detail["revision"] for e in entries] == [1, 2]
    assert entries[1].detail["added"] == [rec.history[-1]]  # only what that save added
    assert store.audit.verify().ok


def test_the_pause_is_written(store):
    store.pause("a canary was missed")
    store.reopen()
    assert [e.action for e in store.audit.entries()][-2:] == ["queue paused", "queue reopened"]


def test_a_refused_save_writes_nothing(store):
    from cap.store import PostConflictError

    rec = PostRecord(channel_id=Channel.A)
    store.put(rec)
    stale = store.get(rec.post_id)
    store.put(store.get(rec.post_id))
    with pytest.raises(PostConflictError):
        store.put(stale)
    assert len(store.audit.entries(rec.post_id)) == 2
    assert store.audit.verify().ok


def test_retention_removes_old_entries_and_the_chain_still_holds(monkeypatch):
    import cap.audit as audit

    log = MemoryAuditLog()
    real = audit.datetime

    class Then(real):  # type: ignore[misc, valid-type]
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return real.now(tz) - timedelta(days=100)

    monkeypatch.setattr(audit, "datetime", Then)
    for i in range(3):
        log.append("event", detail={"n": i})  # written 100 days ago
    monkeypatch.setattr(audit, "datetime", real)
    for i in range(3, 5):
        log.append("event", detail={"n": i})
    assert log.verify().ok
    assert log.purge(90) == 3
    assert [e.detail["n"] for e in log.entries()] == [3, 4]
    assert log.verify().ok
    log.append("after the purge")
    assert log.verify().ok


# ---- Postgres: the table refuses edits, and the chain finds edits made around that --


def _superuser(pg_url) -> psycopg.Connection:
    return psycopg.connect(pg_url, autocommit=True)


@pytest.fixture
def pg_store(pg_url):
    from cap.store.postgres import PostgresStore

    s = PostgresStore.open(pg_url)
    for i in range(4):
        s.audit.append("event", detail={"n": i})
    yield s
    s.close()


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE audit_log SET actor = 'someone' WHERE id = 2",
        "DELETE FROM audit_log WHERE id = 2",
        "TRUNCATE audit_log",
    ],
)
def test_the_table_refuses_changes(pg_store, pg_url, sql):
    with _superuser(pg_url) as c, pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        c.execute(sql)
    assert pg_store.audit.verify().ok


@pytest.mark.parametrize(
    "tamper,why",
    [
        ("UPDATE audit_log SET detail = '{\"n\": 99}' WHERE id = 2", "entry 2 was changed"),
        ("DELETE FROM audit_log WHERE id = 3", "entry 3 is missing"),
    ],
)
def test_a_change_made_with_the_guard_switched_off_is_found(pg_store, pg_url, tamper, why):
    with _superuser(pg_url) as c:
        c.execute("ALTER TABLE audit_log DISABLE TRIGGER USER")
        c.execute(tamper)
        c.execute("ALTER TABLE audit_log ENABLE TRIGGER USER")
    check = pg_store.audit.verify()
    assert not check.ok and why in check.why


def test_purge_on_postgres_re_anchors_the_chain(pg_store, pg_url):
    with _superuser(pg_url) as c:
        c.execute("ALTER TABLE audit_log DISABLE TRIGGER USER")
        # entries written 100 days ago: rewrite them with their hashes as of then
        rows = c.execute(
            "SELECT id, actor, action, post_id, detail FROM audit_log ORDER BY id"
        ).fetchall()
        from cap.audit import entry_hash

        prev = ""
        for i, (id_, actor, action, post_id, detail) in enumerate(rows):
            at = datetime.now(UTC) - timedelta(days=100 if i < 2 else 0)
            h = entry_hash(prev, id_, at, actor, action, post_id, detail)
            c.execute(
                "UPDATE audit_log SET at = %s, prev_hash = %s, hash = %s WHERE id = %s",
                (at, prev, h, id_),
            )
            prev = h
        c.execute("ALTER TABLE audit_log ENABLE TRIGGER USER")
    assert pg_store.audit.verify().ok
    assert pg_store.audit.purge(90) == 2
    assert [e.id for e in pg_store.audit.entries()] == [3, 4]
    assert pg_store.audit.verify().ok
    pg_store.audit.append("after the purge")
    assert pg_store.audit.verify().ok


# ---- the app: who signs in, and a broken chain shown on the Operator screen ------


def test_sign_in_and_a_save_through_the_app_name_the_person(world):  # noqa: F811
    store, api = world
    # signing in happens before there is a session, so the entry names who signed in
    assert any(
        e.action == "signed in" and e.detail == {"who": "hemant"} for e in store.audit.entries()
    )
    post = next(p for p in store.queue())
    api.get(f"/posts/{post.post_id}")
    api.form(f"/posts/{post.post_id}/verdict", decision="reject", reason_code="other")
    last = store.audit.entries(post.post_id)[-1]
    assert last.actor == "hemant" and last.action == "post saved"


def test_the_operator_screen_says_when_the_chain_is_broken(world):  # noqa: F811
    store, api = world
    api.app.state.audit_check = type(store.audit.verify())(False, 3, 2, "entry 2 was changed")
    html = api.get("/operator").text
    assert "The audit log has been altered" in html and "entry 2 was changed" in html
