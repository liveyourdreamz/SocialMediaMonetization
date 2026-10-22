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


# ---- re-verification findings, 22 Oct ------------------------------------------------


def test_finalising_an_approval_writes_the_history(tmp_path: Path, backend, monkeypatch) -> None:
    """R6: deleting the record_approved call failed no test. Approve through the real
    card path and the post must be in the store's history."""
    from cap.approval import release_job
    from cap.approval.app import finalise_and_issue
    from cap.approval.card import prepare_card, record_verdict
    from cap.schema.post_record import Decision

    backend("anthropic")
    monkeypatch.setattr(release_job, "start", lambda store, record: None)  # no render here
    store = FileStore(tmp_path / "data")
    rec = _approved_post()
    rec.status = PostStatus.AWAITING_APPROVAL
    for g in (rec.gates.originality, rec.gates.facts):
        g.passed = True
    rec.gates.ethics.verdict = rec.gates.ethics.verdict.__class__("pass")
    rec.gates.disclosure.passed, rec.gates.disclosure.mfk_band = True, "clear"
    prepare_card(rec)
    store.put(rec)
    record_verdict(
        rec,
        Decision.APPROVE,
        reviewer="hemant",
        time_on_card_s=rec.approval.expected_reading_s,
        opening_line="Here is the thing.",
        spot_check_supported=True if rec.approval.spot_check else None,
    )
    finalise_and_issue(store, rec, "hemant")
    assert [pid for pid, _ in store.history().recent("B", 90)] == [rec.post_id]


def test_one_cut_line_in_the_history_never_stops_a_brief(tmp_path: Path) -> None:
    """A write cut through an Arabic character spoils only its own line."""
    h = FileHistory(tmp_path / "history.jsonl")
    good = '{"post_id":"a","channel":"C","script":"ok","at":"2026-10-22T00:00:00+00:00"}\n'
    h.path.write_bytes(good.encode() + b'{"post_id":"x","script":"\xd9')
    assert [pid for pid, _ in h.recent("C", 90_000)] == ["a"]


def test_a_failed_platform_is_retried_after_another_succeeded(tmp_path: Path) -> None:
    """Re-verification: once TikTok took the post, a YouTube failure could never be
    retried. A retry sends only what has no id yet."""
    from cap.publisher import Upload as Up
    from cap.publisher import publish_to
    from tests.releases import sealed

    class Fake:
        leaves_machine = True

        def __init__(self, name: str, fail: bool) -> None:
            self.name, self.fail, self.calls = name, fail, 0

        def upload(self, manifest):  # type: ignore[no-untyped-def]
            self.calls += 1
            if self.fail:
                raise RuntimeError("quota")
            return Up(f"{self.name}-1", "")

    r, t, _ = sealed(tmp_path)
    tiktok, yt = Fake("tiktok", False), Fake("youtube", True)
    publish_to(r, t, [tiktok, yt])
    assert r.status == PostStatus.PUBLISHED and "youtube" in r.publish.errors
    yt.fail = False
    publish_to(r, t, [tiktok, yt])
    assert r.publish.platform_post_ids["youtube"] == "youtube-1"
    assert tiktok.calls == 1 and yt.calls == 2  # TikTok is never sent twice


def test_facebook_and_instagram_are_separate_destinations(tmp_path: Path, monkeypatch) -> None:
    """One adapter per destination, so a retry after an Instagram failure never posts
    the Facebook Reel twice (re-verification, 22 Oct)."""
    from cap import connections
    from cap.publisher import adapters_for

    page = {"account": {"id": "p1", "page_token": "t", "instagram_id": "ig1"}}
    monkeypatch.setattr(connections, "load", lambda platform: page)
    names = [a.name for a in adapters_for(["facebook", "instagram"], tmp_path)]
    assert names == ["local", "facebook", "instagram"]


def test_a_bom_at_the_top_of_env_never_creates_a_second_signing_key(tmp_path: Path) -> None:
    from cap.setup import ensure_local_secrets

    env = tmp_path / ".env"
    env.write_bytes(b"\xef\xbb\xbfCAP_APPROVAL_SIGNING_KEY=" + b"a" * 64 + b"\n")
    ensure_local_secrets(env)
    text = env.read_text(encoding="utf-8-sig")
    assert text.count("CAP_APPROVAL_SIGNING_KEY=") == 1 and ("a" * 64) in text
