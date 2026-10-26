"""The approval token and the publisher's release rule (review C2, C3, H5, T1-T4).

Every test breaks one thing about an approved, sealed release and asserts on the
outcome: the refusal, what an adapter received, which files were written.
"""

from __future__ import annotations

import hashlib
import hmac
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cap import release
from cap.config import get_settings
from cap.domain import token as tok
from cap.policy import TOKEN_TTL_HOURS
from cap.publisher import (
    DryRunAdapter,
    LocalFolderAdapter,
    PublishRefusedError,
    Upload,
    publish,
    publish_to,
)
from cap.schema.post_record import Channel, Decision, PostStatus, Sponsor
from tests.releases import sealed


class Recording:
    """A platform adapter that records what it was given."""

    leaves_machine = True

    def __init__(self, name: str = "youtube", fail: bool = False) -> None:
        self.name, self.fail = name, fail
        self.got: list[release.Manifest] = []

    def upload(self, manifest):
        self.got.append(manifest)
        if self.fail:
            raise RuntimeError("platform said no")
        return Upload(f"{self.name}-1", f"https://example.test/{self.name}")


def _resign(t: tok.ApprovalToken, **change) -> tok.ApprovalToken:
    """A token changed and signed with the real key: only the binding checks remain."""
    raw = tok.ApprovalToken(**{**t.__dict__, **change, "signature": ""})
    sig = hmac.new(
        get_settings().approval_signing_key.get_secret_value().encode(),
        raw.payload(),
        hashlib.sha256,
    ).hexdigest()
    return tok.ApprovalToken(**{**raw.__dict__, "signature": sig})


# ---- the token itself -----------------------------------------------------------


def test_token_round_trip_and_binding(tmp_path):
    r, t, _ = sealed(tmp_path)
    h = r.release.manifest_sha256
    assert tok.verify(t, r.post_id, h) == (True, "ok")
    forged = tok.ApprovalToken(**{**t.__dict__, "post_id": "other"})
    assert tok.verify(forged, "other", h) == (False, "bad signature")
    assert tok.verify(t, r.post_id, "0" * 64)[1].startswith("manifest hash")
    with pytest.raises(PermissionError):
        tok.issue(r.post_id, h, "intruder", "approve", canary=False)
    assert tok.ApprovalToken.decode(t.encode()) == t


def test_a_token_from_before_the_manifest_is_refused_not_crashed(tmp_path):
    r, t, _ = sealed(tmp_path)
    old = t.encode().replace('"manifest_sha256"', '"asset_sha256"')
    with pytest.raises(ValueError):
        tok.ApprovalToken.decode(old)
    r.approval.token = old
    with pytest.raises(PublishRefusedError, match="no approval token"):
        publish(r, release.decode_token(r), DryRunAdapter())


def test_t1_a_valid_token_for_another_post_with_the_same_hash_is_refused(tmp_path):
    r, t, _ = sealed(tmp_path)
    same_hash = r.release.manifest_sha256
    assert tok.verify(t, "another-post", same_hash) == (False, "token is for another post")


def test_t2_the_reviewer_is_checked_again_at_verify_time(tmp_path, monkeypatch):
    r, t, _ = sealed(tmp_path)
    monkeypatch.setenv("CAP_APPROVERS", '["sara"]')  # hemant is no longer an approver
    get_settings.cache_clear()
    ok, reason = tok.verify(t, r.post_id, r.release.manifest_sha256)
    assert not ok and reason == "token reviewer is not an authorised approver"


def test_t3_expiry_is_enforced_at_exactly_the_configured_lifetime(tmp_path):
    r, _, _ = sealed(tmp_path)
    t0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    t = tok.issue(r.post_id, r.release.manifest_sha256, "hemant", "approve", False, now=t0)
    life = timedelta(hours=TOKEN_TTL_HOURS)
    h = r.release.manifest_sha256
    assert tok.verify(t, r.post_id, h, now=t0 + life - timedelta(seconds=1)) == (True, "ok")
    assert tok.verify(t, r.post_id, h, now=t0 + life) == (False, tok.EXPIRED)
    assert tok.verify(t, r.post_id, h, now=t0 + life + timedelta(seconds=1)) == (False, tok.EXPIRED)


@pytest.mark.parametrize("decision", ["reject", "escalate"])
def test_t4_a_reject_or_escalate_token_never_verifies_for_publishing(tmp_path, decision):
    r, _, _ = sealed(tmp_path)
    t = tok.issue(r.post_id, r.release.manifest_sha256, "hemant", decision, canary=False)
    ok, reason = tok.verify(t, r.post_id, r.release.manifest_sha256)
    assert not ok and reason == f"decision is {decision}, not approve"
    with pytest.raises(PublishRefusedError, match="not approve"):
        publish(r, t, DryRunAdapter())


def test_a_token_with_a_naive_issue_time_is_refused_not_crashed(tmp_path):
    r, t, _ = sealed(tmp_path)
    naive = _resign(t, issued_at="2026-10-22T00:00:00")
    assert tok.verify(naive, r.post_id, r.release.manifest_sha256) == (
        False,
        "token has no valid issue time",
    )


def test_canary_token_never_verifies(tmp_path):
    r, _, _ = sealed(tmp_path)
    t = tok.issue(r.post_id, r.release.manifest_sha256, "hemant", "approve", canary=True)
    ok, reason = tok.verify(t, r.post_id, r.release.manifest_sha256)
    assert not ok and "canary" in reason


# ---- C2: the manifest binds everything that leaves -----------------------------


def test_publisher_happy_path_hands_the_adapter_the_verified_manifest(tmp_path):
    r, t, files = sealed(tmp_path)
    yt = Recording()
    out = publish_to(r, t, [LocalFolderAdapter(tmp_path / "pub"), yt])
    assert out.status == PostStatus.PUBLISHED
    m = yt.got[0]
    assert isinstance(m, release.Manifest) and m.sha256 == r.release.manifest_sha256
    assert {f.role: f.path for f in m.files} == files
    assert out.publish.platform_post_ids["youtube"] == "youtube-1"
    pub = tmp_path / "pub" / r.post_id
    assert sorted(p.name for p in pub.iterdir()) == [
        "long.mp4",
        "manifest.json",
        "short.mp4",
        "thumbnail.png",
    ]
    import json

    written = json.loads((pub / "manifest.json").read_text(encoding="utf-8"))
    assert written["manifest_sha256"] == r.release.manifest_sha256
    assert release.canonical_bytes(written["manifest"]) == release.canonical_bytes(m.document())


def test_the_manifest_is_canonical_and_does_not_depend_on_where_the_files_are(tmp_path):
    r, _, files = sealed(tmp_path)
    a, b = release.build_manifest(r), release.build_manifest(r)
    assert a.sha256 == b.sha256 == r.release.manifest_sha256
    doc = a.document()
    assert set(doc["files"]) == {"long", "short", "thumbnail"}
    assert {v["platform"] for v in doc["variants"]} == {"youtube_long", "youtube_short"}
    assert set(doc["flags"]) == {"made_for_kids", "synthetic_media", "paid_promotion"}
    moved = tmp_path / "moved"
    moved.mkdir()
    for f in r.release.files:
        dest = moved / Path(f.uri).name
        Path(f.uri).rename(dest)
        f.uri = str(dest)
    assert release.build_manifest(r).sha256 == a.sha256


def _variant(r, platform: str):
    return next(v for v in r.variants if v.platform.value == platform)


def _swap_short_path(r, files) -> None:
    evil = files["short"].with_name("evil.mp4")
    evil.write_bytes(b"evil")
    next(f for f in r.release.files if f.role == "short").uri = str(evil)


def _caption(r, files) -> None:
    r.draft.caption = _variant(r, "youtube_long").caption = "TAMPERED caption"


def _synthetic_media(r, files) -> None:
    r.gates.disclosure.ai_label = False
    r.media_manifest.synthetic_elements_present = False


# every change a release must not survive, by name
_TAMPER = {
    "short_file": lambda r, f: f["short"].write_bytes(b"NOT THE APPROVED SHORT"),
    "thumbnail_file": lambda r, f: f["thumbnail"].write_bytes(b"another picture"),
    "long_file": lambda r, f: f["long"].write_bytes(b"re-rendered"),
    "swap_short_path": _swap_short_path,
    "title": lambda r, f: setattr(
        _variant(r, "youtube_long"), "title", "TAMPERED TITLE http://evil.example"
    ),
    "short_title": lambda r, f: setattr(
        _variant(r, "youtube_short"), "title", "Different Short title"
    ),
    "caption": _caption,
    "short_caption": lambda r, f: setattr(
        _variant(r, "youtube_short"), "caption", "TAMPERED short caption"
    ),
    "tags": lambda r, f: setattr(r.brief, "topic", "completely different topic words"),
    "made_for_kids": lambda r, f: setattr(r.gates.disclosure, "made_for_kids", True),
    "synthetic_media": _synthetic_media,
    "paid_promotion": lambda r, f: setattr(r.gates.disclosure, "paid_promo", True),
    "opening_line": lambda r, f: setattr(r.human_contribution, "text", "a different line"),
}


def _tamper(r, files, what: str) -> None:
    _TAMPER[what](r, files)


@pytest.mark.parametrize(
    "what",
    [
        "short_file",
        "thumbnail_file",
        "long_file",
        "swap_short_path",
        "title",
        "short_title",
        "caption",
        "short_caption",
        "tags",
        "made_for_kids",
        "synthetic_media",
        "paid_promotion",
        "opening_line",
    ],
)
def test_any_change_after_approval_is_refused_and_nothing_is_sent(tmp_path, what):
    r, t, files = sealed(tmp_path)
    _tamper(r, files, what)
    local, yt = LocalFolderAdapter(tmp_path / "pub"), Recording()
    # turning paid promotion on after approval is also refused for its missing Ad prefix
    with pytest.raises(PublishRefusedError, match="changed since it was approved|Ad prefix"):
        publish_to(r, t, [local, yt])
    assert yt.got == [] and not (tmp_path / "pub").exists()
    assert r.status == PostStatus.APPROVED


def test_a_missing_final_file_is_refused(tmp_path):
    r, t, files = sealed(tmp_path)
    files["thumbnail"].unlink()
    with pytest.raises(PublishRefusedError, match="thumbnail file is missing"):
        publish(r, t, DryRunAdapter())


# ---- C3: the publisher re-verifies the approval itself ------------------------


# every way an approval can fail to hold, by name
_BREAK = {
    "no_decision": lambda r: setattr(r.approval, "decision", None),
    "rejected_on_card": lambda r: setattr(r.approval, "decision", Decision.REJECT),
    "never_finalised": lambda r: setattr(r.approval, "decided_at", None),
    "no_opening_line": lambda r: setattr(r.human_contribution, "text", "  "),
    "reviewer_not_approver": lambda r: setattr(r.approval, "reviewer", "intruder"),
    "canary": lambda r: setattr(r.flags, "canary", True),
    "gate_failed": lambda r: setattr(r.gates.facts, "passed", False),
    "not_approved": lambda r: setattr(r, "status", PostStatus.AWAITING_APPROVAL),
    "no_permit": lambda r: setattr(
        r, "sponsor", Sponsor(sponsor_id="s", name="NoteCo", permit_status="applied")
    ),
    "release_not_ready": lambda r: setattr(r.release, "status", release.ReleaseStatus.PREPARING),
}


def _break(r, what: str) -> None:
    _BREAK[what](r)


@pytest.mark.parametrize(
    "what",
    [
        "no_decision",
        "rejected_on_card",
        "never_finalised",
        "no_opening_line",
        "reviewer_not_approver",
        "canary",
        "gate_failed",
        "not_approved",
        "no_permit",
        "release_not_ready",
    ],
)
def test_a_valid_token_is_not_enough_the_approval_is_rechecked(tmp_path, what):
    r, t, _ = sealed(tmp_path)
    _break(r, what)
    yt = Recording()
    with pytest.raises(PublishRefusedError):
        publish_to(r, t, [LocalFolderAdapter(tmp_path / "pub"), yt])
    assert yt.got == [] and r.status != PostStatus.PUBLISHED


def test_the_token_reviewer_must_be_the_card_reviewer(tmp_path, monkeypatch):
    monkeypatch.setenv("CAP_APPROVERS", '["hemant", "sara"]')
    get_settings.cache_clear()
    r, _, _ = sealed(tmp_path)
    by_sara = tok.issue(r.post_id, r.release.manifest_sha256, "sara", "approve", canary=False)
    with pytest.raises(PublishRefusedError, match="different reviewer"):
        publish(r, by_sara, DryRunAdapter())


def test_channel_c_needs_a_stage_one_by_an_arabic_reviewer_at_the_publisher(tmp_path):
    r, t, _ = sealed(tmp_path, Channel.C)
    assert publish(r.model_copy(deep=True), t, DryRunAdapter()).status == PostStatus.PUBLISHED
    no_stage_one = r.model_copy(deep=True)
    no_stage_one.approval.stage_one = None
    with pytest.raises(PublishRefusedError, match="stage-one"):
        publish(no_stage_one, t, DryRunAdapter())
    wrong_person = r.model_copy(deep=True)
    assert wrong_person.approval.stage_one is not None
    wrong_person.approval.stage_one.reviewer = "hemant"
    with pytest.raises(PublishRefusedError, match="stage-one"):
        publish(wrong_person, t, DryRunAdapter())


@pytest.mark.parametrize("flag", ["tour", "canary"])
def test_tour_and_canary_are_refused_for_every_adapter_not_only_the_first(tmp_path, flag):
    r, t, _ = sealed(tmp_path)
    setattr(r.flags, flag, True)
    yt = Recording()
    with pytest.raises(PublishRefusedError):
        publish_to(r, t, [LocalFolderAdapter(tmp_path / "pub"), yt])
    assert yt.got == [] and r.status == PostStatus.APPROVED


def test_a_tour_post_may_still_go_to_the_local_folder(tmp_path):
    r, t, _ = sealed(tmp_path)
    r.flags.tour = True
    out = publish_to(r, t, [LocalFolderAdapter(tmp_path / "pub")])
    assert out.status == PostStatus.PUBLISHED and (tmp_path / "pub" / r.post_id).is_dir()


# ---- H5: no duplicate uploads -------------------------------------------------


@pytest.fixture(params=["memory", "postgres"])
def pub_store(request):
    from cap.store import MemoryStore

    if request.param == "memory":
        yield MemoryStore()
        return
    from cap.store.postgres import PostgresStore

    s = PostgresStore.open(request.getfixturevalue("pg_url"))
    try:
        yield s
    finally:
        s.close()


def test_two_concurrent_publishes_upload_once(tmp_path, pub_store):
    """Review R8: one publish of a post at a time across processes (the store's lock),
    and the post is read inside it."""
    from cap.approval.app import publish_now

    r, _, _ = sealed(tmp_path)
    pub_store.put(r)
    gate = threading.Event()
    calls: list[str] = []

    class Slow(Recording):
        def upload(self, manifest):
            calls.append(manifest.post_id)
            gate.wait(5)
            return "yt-1"

    slow = Slow()
    outcomes: list[str | None] = []
    first = threading.Thread(
        target=lambda: outcomes.append(publish_now(pub_store, r.post_id, publisher=slow))
    )
    first.start()
    deadline = time.time() + 5
    while not calls and time.time() < deadline:
        time.sleep(0.01)
    outcomes.append(publish_now(pub_store, r.post_id, publisher=slow))  # while the first uploads
    gate.set()
    first.join(5)
    assert calls == [r.post_id]
    assert sorted(outcomes, key=str) == [None, "a publish of this post is already running"]
    assert pub_store.get(r.post_id).publish.platform_post_ids == {"youtube": "yt-1"}
    assert publish_now(pub_store, r.post_id, publisher=slow) is None  # a later click
    assert calls == [r.post_id]  # sends nothing again


def test_a_save_conflict_after_an_upload_keeps_the_platform_id(tmp_path):
    """Review R8: a note saved by someone else mid-publish must not cost a sent id."""
    from cap.approval.app import publish_now
    from cap.store import MemoryStore

    store = MemoryStore()
    r, _, _ = sealed(tmp_path)
    store.put(r)

    class Noisy(Recording):
        def upload(self, manifest):
            store.update(r.post_id, lambda rec: rec.log("a worker note"))  # someone else saves
            return super().upload(manifest)

    assert publish_now(store, r.post_id, publisher=Noisy()) is None
    got = store.get(r.post_id)
    assert got.publish.platform_post_ids == {"youtube": "youtube-1"}
    assert got.status == PostStatus.PUBLISHED and any("a worker note" in h for h in got.history)


def test_each_result_is_saved_at_once_and_a_retry_skips_what_already_went(tmp_path):
    r, t, _ = sealed(tmp_path)
    saved: list[dict[str, str]] = []
    long_cut, short_cut = Recording("youtube"), Recording("youtube_short")

    class Crash(Recording):
        def upload(self, manifest):
            raise KeyboardInterrupt  # the app dies between two uploads

    with pytest.raises(KeyboardInterrupt):
        publish_to(
            r,
            t,
            [long_cut, Crash("youtube_short")],
            save=lambda rec: saved.append(dict(rec.publish.platform_post_ids)),
        )
    assert saved[-1] == {"youtube": "youtube-1"}  # on disk before the crash
    assert r.status == PostStatus.APPROVED and r.publish.sending == ["youtube_short"]
    # the app died mid-upload: the next publish says so and sends nothing there (R8)
    out = publish_to(r, t, [long_cut, short_cut])
    assert short_cut.got == [] and "interrupted" in out.publish.errors["youtube_short"]
    assert out.publish.sending == []
    out = publish_to(out, t, [long_cut, short_cut])  # Hemant checked; publish again
    assert len(long_cut.got) == 1 and len(short_cut.got) == 1
    assert out.status == PostStatus.PUBLISHED and set(out.publish.platform_post_ids) == {
        "youtube",
        "youtube_short",
    }


def test_every_platform_failing_leaves_it_approved_and_the_retry_sends_only_what_failed(tmp_path):
    r, t, _ = sealed(tmp_path)
    local = LocalFolderAdapter(tmp_path / "pub")
    out = publish_to(r, t, [local, Recording(fail=True)])
    assert out.status == PostStatus.APPROVED and "youtube" in out.publish.errors
    (tmp_path / "pub" / r.post_id / "long.mp4").unlink()  # was the local copy made again?
    again = publish_to(out, t, [local, Recording()])
    assert again.status == PostStatus.PUBLISHED and "youtube" not in again.publish.errors
    assert not (tmp_path / "pub" / r.post_id / "long.mp4").exists()


# ---- M4: re-issue --------------------------------------------------------------


def test_an_expired_token_is_reissued_only_for_the_unchanged_approved_release(tmp_path):
    r, t, files = sealed(tmp_path)
    old = tok.issue(
        r.post_id,
        r.release.manifest_sha256,
        "hemant",
        "approve",
        False,
        now=datetime.now(UTC) - timedelta(hours=TOKEN_TTL_HOURS + 1),
    )
    r.approval.token = old.encode()
    assert release.token_state(r) == "expired"
    with pytest.raises(PublishRefusedError, match="expired"):
        publish(r, old, DryRunAdapter())
    with pytest.raises(PublishRefusedError, match="only the approver"):
        release.reissue(r, "sara")
    broken = r.model_copy(deep=True)
    broken.approval.decision = None
    with pytest.raises(PublishRefusedError):
        release.reissue(broken, "hemant")
    changed = r.model_copy(deep=True)
    files["short"].write_bytes(b"swapped")
    with pytest.raises(PublishRefusedError, match="changed since"):
        release.reissue(changed, "hemant")
    files["short"].write_bytes(b"short bytes")  # back to what was approved
    fresh = release.reissue(r, "hemant")
    assert release.token_state(r) == "valid"
    assert publish(r, fresh, DryRunAdapter()).status == PostStatus.PUBLISHED
