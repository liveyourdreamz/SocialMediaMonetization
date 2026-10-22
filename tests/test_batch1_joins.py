"""The joins between the batch-1 lanes (merge, 22 Oct): approval feeds the originality
history; every caller uses the one confirmed-sources rule; start-up shows each
person's own sign-in code."""

from __future__ import annotations

from pathlib import Path

import pytest

from cap.approval.app import FileStore, Store
from cap.config import get_settings
from cap.gates.history import FileHistory
from cap.schema.post_record import Channel, Claim, PostRecord, PostStatus


@pytest.fixture
def backend(monkeypatch):
    def set_(name: str) -> None:
        monkeypatch.setenv("CAP_MODEL_BACKEND", name)
        if name == "anthropic":
            monkeypatch.setenv("CAP_ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key")
        get_settings.cache_clear()

    yield set_
    get_settings.cache_clear()


def _approved_post() -> PostRecord:
    rec = PostRecord(channel_id=Channel.B, status=PostStatus.APPROVED)
    rec.draft.title, rec.draft.script = "Cocoons", "From orbit, here's what I see. Silk."
    return rec


@pytest.mark.parametrize("name,enters", [("anthropic", True), ("mock", False)])
def test_an_approval_enters_the_history_unless_it_is_a_mock_draft(
    tmp_path: Path, backend, name: str, enters: bool
) -> None:
    from cap.gates.history import record_approved

    backend(name)
    store = FileStore(tmp_path / "data")
    assert record_approved(_approved_post(), store.history()) is enters
    assert (len(store.history().recent("B", 90)) == 1) is enters


def test_the_file_store_keeps_its_history_beside_its_records(tmp_path: Path) -> None:
    store = FileStore(tmp_path / "data")
    h = store.history()
    assert isinstance(h, FileHistory) and h.path.parent == tmp_path / "data"
    assert Store().history().path.name == "history.jsonl"  # type: ignore[attr-defined]


def test_every_published_source_list_uses_the_one_rule() -> None:
    """The renderer, the outbox package and the release description list the same
    links: supported claims only, never the test log."""
    from cap.gates.verdict import confirmed_sources
    from cap.ui.views import package

    rec = PostRecord(channel_id=Channel.A)
    rec.draft.claims = [
        Claim(text="a", source_url="https://ok.example/a", status="supported"),
        Claim(text="b", source_url="https://no.example/b", status="unsupported"),
        Claim(text="c", source_url="testlog://A/1", status="supported", in_test_log=True),
    ]
    assert confirmed_sources(rec) == ["https://ok.example/a"]
    assert package(rec)["base"]["sources"] == ["https://ok.example/a"]
    import inspect

    import cap.release as rel
    import cap.render as rnd

    for mod in (rel, rnd):
        src = inspect.getsource(mod)
        assert "confirmed_sources" in src and 'startswith("testlog://")' not in src, mod
