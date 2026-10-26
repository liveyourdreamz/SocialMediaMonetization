"""The whole laptop loop on Postgres, the store the app really uses (review 24 Oct: the
pipeline, worker and UI tests ran on memory only, which is how the originality gate
came to read a file nobody wrote to). A brief is filed, drafted by the worker, approved
on the card, made into the final release and published to the local folder; then the
same topic, filed again, is caught by originality against the approved post."""

from __future__ import annotations

import pytest

from cap.approval.app import finalise_and_issue, publish_now
from cap.demo.canary_job import file_brief
from cap.demo.seed import new_brief
from cap.domain.card import record_verdict
from cap.publisher import LocalFolderAdapter
from cap.schema.post_record import Decision, PostStatus, ReleaseStatus
from cap.store.postgres import PostgresStore
from cap.worker import drain


@pytest.fixture
def store(pg_url, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = PostgresStore.open(pg_url)
    yield s
    s.close()


def test_brief_to_published_on_postgres(store, tmp_path):
    rec = new_brief("A", "How a heat pump moves heat")
    file_brief(store, rec, {"urls": [], "test_log": ""})
    drain(store, rec.post_id)
    got = store.get(rec.post_id)
    assert got.status == PostStatus.AWAITING_APPROVAL, got.history[-3:]
    assert got.approval.queued_at is not None

    record_verdict(got, Decision.APPROVE, reviewer="hemant", time_on_card_s=999,
                   opening_line="Hemant here: heat, uphill.",
                   spot_check_supported=True if got.approval.spot_check else None)  # fmt: skip
    assert finalise_and_issue(store, got, "hemant", acknowledged=True)
    drain(store, rec.post_id)  # the final render and seal
    got = store.get(rec.post_id)
    assert got.release.status == ReleaseStatus.READY and got.approval.token

    assert publish_now(store, rec.post_id, publisher=LocalFolderAdapter(tmp_path / "pub")) is None
    assert store.get(rec.post_id).status == PostStatus.PUBLISHED
    assert [pid for pid, _ in store.history().recent("A", 90)] == []  # mock drafts stay out


def test_originality_reads_postgres_history_in_the_real_loop(store, monkeypatch):
    """R1 end to end: an approval written to the Postgres history fails its duplicate."""
    from cap.gates.history import record_approved

    first = new_brief("A", "How a heat pump moves heat")
    file_brief(store, first, {"urls": [], "test_log": ""})
    drain(store, first.post_id)
    done = store.get(first.post_id)
    done.status = PostStatus.APPROVED
    from datetime import UTC, datetime

    done.approval.decided_at = datetime.now(UTC)
    monkeypatch.setenv("CAP_MODEL_BACKEND", "anthropic")  # mock drafts never enter history
    from cap.config import get_settings

    get_settings.cache_clear()
    assert record_approved(done, store.history())
    monkeypatch.setenv("CAP_MODEL_BACKEND", "mock")
    get_settings.cache_clear()

    again = new_brief("A", "How a heat pump moves heat")
    file_brief(store, again, {"urls": [], "test_log": ""})
    drain(store, again.post_id)
    got = store.get(again.post_id)
    assert got.gates.originality.nearest_post_id == first.post_id
    assert got.status == PostStatus.AWAITING_APPROVAL
