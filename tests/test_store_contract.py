"""Every store adapter keeps the same contract (M1 step 2, ADR-006).

The same tests run against the in-memory store, the old JSON files and Postgres, so
the app cannot tell which one it has, and Postgres behaves as the others did.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest

from cap.gates.history import record_approved
from cap.schema.post_record import Channel, PostRecord, PostStatus
from cap.store import MemoryStore, PostConflictError, PostNotFoundError, PostStore
from cap.store.files import FileStore


@pytest.fixture(params=["memory", "files", "postgres"])
def make_store(request, tmp_path):
    """A factory: calling it again opens the same store afresh (an app restart)."""
    opened: list[PostStore] = []
    if request.param == "memory":
        one = MemoryStore()

        def make() -> PostStore:
            return one
    elif request.param == "files":

        def make() -> PostStore:
            return FileStore(tmp_path / "data")
    else:
        from cap.store.postgres import PostgresStore

        url = request.getfixturevalue("pg_url")

        def make() -> PostStore:
            s = PostgresStore.open(url)
            opened.append(s)
            return s

    yield make
    for s in opened:
        s.close()  # type: ignore[attr-defined]


def _post(channel: Channel = Channel.A, status: PostStatus = PostStatus.AWAITING_APPROVAL, **kw):
    return PostRecord(channel_id=channel, status=status, **kw)


def test_put_then_get_returns_the_same_record(make_store):
    store = make_store()
    rec = _post(Channel.C)
    rec.draft.title, rec.draft.script = "الشرانق", "من المدار، هذا ما أراه."
    rec.log("filed")
    store.put(rec)
    got = store.get(rec.post_id)
    assert got.model_dump() == rec.model_dump()


def test_a_missing_post_raises_not_found(make_store):
    with pytest.raises(PostNotFoundError):
        make_store().get("no-such-post")


def test_put_replaces_and_count_and_all_agree(make_store):
    store = make_store()
    a, b = _post(), _post(Channel.B)
    store.put(a)
    store.put(b)
    a.status = PostStatus.APPROVED
    store.put(a)
    assert store.count() == 2
    assert {p.post_id: p.status for p in store.all()} == {
        a.post_id: PostStatus.APPROVED,
        b.post_id: PostStatus.AWAITING_APPROVAL,
    }


def test_the_queue_is_awaiting_posts_oldest_first(make_store):
    store = make_store()
    now = datetime.now(UTC)
    newer = _post(created_at=now)
    older = _post(created_at=now - timedelta(hours=1))
    done = _post(status=PostStatus.PUBLISHED)
    for p in (newer, done, older):
        store.put(p)
    assert [p.post_id for p in store.queue()] == [older.post_id, newer.post_id]


def test_records_survive_a_restart(make_store):
    rec = _post()
    make_store().put(rec)
    assert make_store().get(rec.post_id).post_id == rec.post_id


@pytest.mark.parametrize("status", [PostStatus.DRAFTING, PostStatus.GATED, PostStatus.RENDERING])
def test_the_file_store_parks_a_post_interrupted_by_a_restart_once(make_store, status, request):
    if "files" not in request.node.callspec.id:
        pytest.skip("the worker's start-up recovery decides for Postgres (test_worker.py)")
    rec = _post(status=status)
    make_store().put(rec)
    got = make_store().get(rec.post_id)
    assert got.status == PostStatus.PARKED and "restarted" in (got.flags.stop_reason or "")
    assert make_store().get(rec.post_id).history == got.history  # not parked twice


def test_the_pause_and_analytics_survive_a_restart(make_store, request):
    store = make_store()
    store.pause("a canary was missed")
    store.set_analytics({"views": 12}, "example figures")
    again = make_store()
    assert again.paused and again.paused_reason == "a canary was missed"
    assert (
        again.state.analytics == {"views": 12} and again.state.analytics_note == "example figures"
    )
    again.reopen()
    assert not make_store().paused


def test_time_on_card_starts_at_the_first_open(make_store):
    store = make_store()
    rec = _post()
    store.put(rec)
    assert store.seconds_on_card(rec.post_id, "hemant") == 0
    assert store.open_card(rec.post_id, "hemant") == 0
    assert store.open_card(rec.post_id, "hemant") >= 0
    assert store.seconds_on_card(rec.post_id, "layla") == 0  # per reviewer


def test_concurrent_saves_of_one_post_never_collide(make_store):
    store = make_store()
    rec = _post()
    errors: list[Exception] = []

    store.put(rec)

    def save(i: int) -> None:
        try:
            for n in range(20):
                while True:  # read, change, save; redo on a conflict
                    r = store.get(rec.post_id)
                    r.log(f"writer {i} #{n}")
                    try:
                        store.put(r)
                        break
                    except PostConflictError:
                        continue
        except Exception as e:  # noqa: BLE001 - reported below
            errors.append(e)

    threads = [threading.Thread(target=save, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    got = store.get(rec.post_id)
    assert sum("writer" in h for h in got.history) == 80  # no save lost
    assert got.revision == 81


def test_a_save_from_a_stale_copy_is_refused(make_store):
    store = make_store()
    rec = _post()
    store.put(rec)
    first, second = store.get(rec.post_id), store.get(rec.post_id)
    first.log("first")
    store.put(first)
    second.log("second")
    with pytest.raises(PostConflictError):
        store.put(second)
    assert "second" not in " ".join(store.get(rec.post_id).history)


def test_a_new_post_cannot_overwrite_an_existing_one(make_store):
    store = make_store()
    rec = _post()
    store.put(rec)
    clash = rec.model_copy(update={"revision": 0})
    with pytest.raises(PostConflictError):
        store.put(clash)


def test_the_originality_history_keeps_approved_posts_once(make_store, monkeypatch):
    from cap.config import ModelBackend, get_settings

    monkeypatch.setenv("CAP_MODEL_BACKEND", ModelBackend.ANTHROPIC.value)
    get_settings.cache_clear()
    store = make_store()
    hist = store.history()
    approved = _post(status=PostStatus.APPROVED)
    approved.draft.script = "Silk is a protein fibre."
    canary = _post(status=PostStatus.APPROVED)
    canary.flags.canary = True
    assert record_approved(approved, hist) and record_approved(approved, hist)
    assert not record_approved(canary, hist)
    assert hist.recent("A", 90).count((approved.post_id, "Silk is a protein fibre.")) == 1
    get_settings.cache_clear()


def test_postgres_migrations_run_once(pg_url):
    from cap.store import migrations
    from cap.store.postgres import PostgresStore, connect

    PostgresStore.open(pg_url).close()
    pool = connect(pg_url)
    with pool.connection() as conn:
        assert migrations.pending(conn) == []
        assert migrations.apply(conn) == []
    pool.close()


def test_postgres_skips_a_row_it_cannot_read_and_keeps_it(pg_url):
    import psycopg

    from cap.store.postgres import PostgresStore

    store = PostgresStore.open(pg_url)
    good = _post()
    store.put(good)
    with psycopg.connect(pg_url, autocommit=True) as c:
        c.execute(
            "INSERT INTO posts (post_id, channel, status, created_at, revision, record)"
            " VALUES ('bad', 'A', 'awaiting_approval', now(), 1, '{\"status\": 42}')"
        )
    assert [p.post_id for p in store.queue()] == [good.post_id]
    assert store.unreadable == ["bad"]
    with psycopg.connect(pg_url) as c:
        assert c.execute("SELECT count(*) FROM posts").fetchone()[0] == 2  # nothing deleted
    store.close()
