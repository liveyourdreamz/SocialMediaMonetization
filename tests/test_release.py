"""Review C1, C2 (adapters), M4 (the Outbox), H5 (the handler) and the honest labels:
the opening line reaches the published video, the Outbox shows the release's state,
the adapters send only what the manifest says.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from cap import release
from cap.approval import release_job
from cap.approval.app import finalise_and_issue, publish_now
from cap.config import get_settings
from cap.domain.card import record_verdict
from cap.pipeline import run_post
from cap.publisher import LocalFolderAdapter, youtube
from cap.schema.post_record import Decision, PostStatus, ReleaseStatus
from cap.server import create_app
from cap.store import MemoryStore
from tests.conftest import SOURCES, drain, make_deps
from tests.releases import sealed
from tests.test_ui import Browser

LINE = "Hemant here: one week, one surprise."


def _queued(client, record, tmp_path):
    out = run_post(record, SOURCES, make_deps(client, record, tmp_path))
    assert out.status == PostStatus.AWAITING_APPROVAL, out.history[-3:]
    return out


def _approve(store, rec, line: str = LINE) -> None:
    record_verdict(
        rec,
        Decision.APPROVE,
        reviewer="hemant",
        time_on_card_s=999,
        opening_line=line,
        spot_check_supported=True if rec.approval.spot_check else None,
    )
    assert finalise_and_issue(store, rec, "hemant", acknowledged=True)


# ---- C1: review render, then the final render with the line --------------------


def test_the_review_render_shows_a_placeholder_and_the_published_file_speaks_the_line(
    client, record, tmp_path
):
    store = MemoryStore()
    rec = _queued(client, record, tmp_path)
    review = Path(rec.asset.uri).read_text(encoding="utf-8")
    assert "[Your opening line goes here]" in review and "[[HEMANT" not in review
    store.put(rec)
    _approve(store, rec)
    drain(store, rec.post_id)
    rec = store.get(rec.post_id)
    assert rec.release.status == ReleaseStatus.READY and rec.approval.token
    final = Path(next(f.uri for f in rec.release.files if f.role == "long"))
    assert final != Path(rec.asset.uri) and LINE in final.read_text(encoding="utf-8")
    assert (
        publish_now(store, rec, publisher=LocalFolderAdapter(tmp_path / "pub")) is None
        and rec.status == PostStatus.PUBLISHED
    )
    published = tmp_path / "pub" / rec.post_id / final.name
    assert LINE in published.read_text(encoding="utf-8")


def test_the_video_final_render_speaks_the_line_the_review_render_does_not(
    client, record, tmp_path, monkeypatch
):
    monkeypatch.setenv("CAP_RENDERER", "video")
    monkeypatch.setenv("CAP_DATA_DIR", str(tmp_path / "d"))
    get_settings.cache_clear()
    import cap.render as renderer

    spoken: list[str] = []
    real = renderer.silent_voice

    def listening(text, voice, out):
        spoken.append(text)
        real(text, voice, out)

    monkeypatch.setattr(renderer, "silent_voice", listening)
    store = MemoryStore()
    rec = _queued(client, record, tmp_path)
    assert not any(LINE in s for s in spoken)
    assert not any("opening line goes here" in s for s in spoken)  # a silent card, not voiced
    review_hash = rec.asset.sha256
    spoken.clear()
    store.put(rec)
    _approve(store, rec)
    drain(store, rec.post_id)
    rec = store.get(rec.post_id)
    assert rec.release.status == ReleaseStatus.READY, rec.release.error
    assert any(LINE in s for s in spoken)
    roles = {f.role: Path(f.uri) for f in rec.release.files}
    assert roles["long"].suffix == ".mp4" and roles["long"].exists() and "thumbnail" in roles
    assert release.build_manifest(rec).file("long").sha256 != review_hash
    # the Outbox plays the final file
    api = Browser(create_app(store), follow_redirects=False)
    api.sign_in("hemant")
    html = api.get(f"/posts/{rec.post_id}/preview").text
    src = f"/assets/{rec.post_id}/final/{roles['long'].name}"
    assert src in html and "Publish now" in html
    assert api.get(src).content == roles["long"].read_bytes()


def test_publishing_is_refused_while_the_final_video_is_being_made(
    client, record, tmp_path, monkeypatch
):
    from cap.render import package

    gate = threading.Event()
    real = package.render_stage

    def slow(*a, **k):
        gate.wait(10)
        return real(*a, **k)

    monkeypatch.setattr(release_job, "render_stage", slow)
    store = MemoryStore()
    rec = _queued(client, record, tmp_path)
    store.put(rec)
    _approve(store, rec)
    try:
        assert rec.status == PostStatus.APPROVED
        assert rec.release.status == ReleaseStatus.PREPARING and not rec.approval.token
        refused = publish_now(store, rec, publisher=LocalFolderAdapter(tmp_path / "pub"))
        assert refused and "not ready" in refused and not (tmp_path / "pub").exists()
        api = Browser(create_app(store), follow_redirects=False)
        api.sign_in("hemant")
        html = api.get(f"/posts/{rec.post_id}/preview").text
        assert "Preparing the final video" in html and "Publish now" not in html
    finally:
        gate.set()
        drain(store, rec.post_id)
        rec = store.get(rec.post_id)
    assert rec.release.status == ReleaseStatus.READY
    assert publish_now(store, rec) is None and rec.status == PostStatus.PUBLISHED


def test_a_failed_final_render_stays_approved_without_a_token_and_can_be_retried(
    client, record, tmp_path, monkeypatch
):
    from cap.render import package

    real = package.render_stage
    attempts: list[int] = []

    def flaky(*a, **k):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("edge-tts timed out")
        return real(*a, **k)

    monkeypatch.setattr(release_job, "render_stage", flaky)
    store = MemoryStore()
    rec = _queued(client, record, tmp_path)
    store.put(rec)
    _approve(store, rec)
    drain(store, rec.post_id)
    rec = store.get(rec.post_id)
    assert rec.status == PostStatus.APPROVED and rec.release.status == ReleaseStatus.FAILED
    assert rec.approval.token == "" and "edge-tts timed out" in rec.release.error
    api = Browser(create_app(store), follow_redirects=False)
    api.sign_in("hemant")
    html = api.get(f"/posts/{rec.post_id}/preview").text
    assert "edge-tts timed out" in html and "Make the final video again" in html
    assert "Publish now" not in html
    r = api.form(f"/posts/{rec.post_id}/release/retry")
    assert r.status_code == 303 and "refused" not in r.headers["location"]
    drain(store, rec.post_id)
    rec = store.get(rec.post_id)
    assert rec.release.status == ReleaseStatus.READY and rec.approval.token
    assert rec.release.attempts == 2
    assert "Publish now" in api.get(f"/posts/{rec.post_id}/preview").text


# ---- M4: the Outbox re-issues an expired token ---------------------------------


def test_the_outbox_reissues_an_expired_token(tmp_path):
    from datetime import UTC, datetime, timedelta

    from cap.domain import token as tok
    from cap.policy import TOKEN_TTL_HOURS

    store = MemoryStore()
    r, _, _ = sealed(tmp_path)
    r.approval.token = tok.issue(
        r.post_id,
        r.release.manifest_sha256,
        "hemant",
        "approve",
        False,
        now=datetime.now(UTC) - timedelta(hours=TOKEN_TTL_HOURS),
    ).encode()
    store.put(r)
    api = Browser(create_app(store), follow_redirects=False)
    api.sign_in("hemant")
    html = api.get(f"/posts/{r.post_id}/preview").text
    assert "Re-issue the token" in html and "Publish now" not in html
    assert api.form(f"/posts/{r.post_id}/release/reissue").status_code == 303
    assert release.token_state(store.get(r.post_id)) == "valid"
    assert "Publish now" in api.get(f"/posts/{r.post_id}/preview").text


# ---- H5: the publish handler does not block the app ----------------------------


def test_the_publish_button_does_not_block_other_requests(tmp_path, monkeypatch):
    store = MemoryStore()
    r, _, _ = sealed(tmp_path)
    store.put(r)
    gate = threading.Event()

    class Slow:
        name = "local"
        leaves_machine = False

        def upload(self, manifest):
            gate.wait(8)
            return "local:x"

    import cap.publisher as pub

    monkeypatch.setattr(pub, "adapters_for", lambda chosen, local: [Slow()])
    api = Browser(create_app(store), follow_redirects=False)
    api.sign_in("hemant")
    publishing = threading.Thread(target=lambda: api.form(f"/posts/{r.post_id}/publish"))
    publishing.start()
    time.sleep(0.3)
    other: list[int] = []
    t = threading.Thread(target=lambda: other.append(api.get("/queue").status_code))
    t.start()
    t.join(3)
    answered_while_publishing = other == [200]
    gate.set()
    publishing.join(10)
    assert answered_while_publishing
    assert store.get(r.post_id).status == PostStatus.PUBLISHED


# ---- C2: the YouTube adapter sends what the manifest says ----------------------


class _FakeYT:
    def __init__(self) -> None:
        self.inserts: list[tuple[str, dict]] = []

    def videos(self):
        yt = self

        class V:
            def insert(self, part, body, media_body):
                yt.inserts.append((part, body))

                class Req:
                    def next_chunk(self):
                        return None, {"id": f"v{len(yt.inserts)}"}

                return Req()

        return V()

    def thumbnails(self):
        class T:
            def set(self, **k):
                class E:
                    def execute(self):
                        return {}

                return E()

        return T()


@pytest.mark.parametrize("kids,paid", [(False, False), (True, True)])
def test_youtube_declarations_come_from_the_manifest(tmp_path, monkeypatch, kids, paid):
    r, _, files = sealed(tmp_path)
    r.gates.disclosure.made_for_kids = kids
    r.gates.disclosure.paid_promo = paid
    m = release.build_manifest(r)
    fake = _FakeYT()
    monkeypatch.setattr(youtube, "_service", lambda: fake)
    up = youtube.YouTubeAdapter("long").upload(m)
    part, body = fake.inserts[0]
    assert body["status"]["selfDeclaredMadeForKids"] is kids
    assert body["status"]["containsSyntheticMedia"] is True
    assert body["paidProductPlacementDetails"]["hasPaidProductPlacement"] is paid
    assert "paidProductPlacementDetails" in part.split(",")
    assert body["snippet"]["title"] == m.text_for("youtube_long").title
    assert up.url.endswith(up.post_id)
    youtube.YouTubeAdapter("short").upload(m)
    _, short_body = fake.inserts[1]
    assert short_body["snippet"]["title"] == "Notely in 60s #Shorts"


def test_youtube_title_and_description_rules():
    long = "Why " + "everything about syncing notes " * 6 + "<finally> works"
    t = youtube.youtube_title(long, short=True)
    assert len(t) <= 100 and t.endswith(" #Shorts") and "<" not in t and ">" not in t
    body = t.removesuffix(" #Shorts")
    assert long.replace("<", "").replace(">", "").startswith(body)  # cut at a word
    assert long.replace("<", "").replace(">", "")[len(body)] == " "
    assert youtube.youtube_title("<b>Short</b> title") == "bShort/b title"
    arabic = "تحلية " * 2000  # ten bytes a word in UTF-8
    d = youtube.youtube_description(arabic + "<script>")
    assert len(d.encode("utf-8")) <= 5000 and "<" not in d
    d.encode("utf-8").decode("utf-8")  # never half a character


# ---- honest labels -------------------------------------------------------------


def test_the_card_says_what_is_true_about_voice_and_provenance(tmp_path, monkeypatch):
    from cap.approval.identity import Reviewer
    from cap.demo.seed import seed_store
    from cap.ui.views import card_context

    monkeypatch.chdir(tmp_path)
    store = MemoryStore()
    seed_store(store)
    rec = next(iter(store.all()))
    rows = dict(card_context(rec, store, Reviewer("hemant", "approver"))["media_rows"])
    assert "ElevenLabs" not in rows["Voice"] and "edge-tts" in rows["Voice"]
    assert "when it is made" in rows["Synthetic label"]  # not signed until the release
    rec.release.c2pa_signed = ["long.mp4"]
    rows = dict(card_context(rec, store, Reviewer("hemant", "approver"))["media_rows"])
    assert "C2PA-signed as AI-generated: long.mp4" in rows["Synthetic label"]
    monkeypatch.setenv("CAP_C2PA", "off")
    get_settings.cache_clear()
    rec.release.c2pa_signed = []
    rows = dict(card_context(rec, store, Reviewer("hemant", "approver"))["media_rows"])
    assert "signing is off" in rows["Synthetic label"]


# ---- C3: `cap check --full` approves through the real card ---------------------


def test_the_full_run_approves_through_the_card_and_publishes_the_line(monkeypatch, tmp_path):
    import json

    from cap import check
    from cap.demo import happy_fixtures
    from tests.test_check import _full_run_env

    _full_run_env(monkeypatch, tmp_path, happy_fixtures)
    lines: list[str] = []
    assert check.full_run(out=lines.append, channels="A") == 0, lines
    [out] = list((tmp_path / "d" / "published" / "full-test").iterdir())
    doc = json.loads((out / "manifest.json").read_text(encoding="utf-8"))["manifest"]
    assert doc["opening_line"] == check.FULL_RUN_OPENING_LINE and doc["approved_by"] == "hemant"
    published = out / doc["files"]["long"]["name"]
    assert check.FULL_RUN_OPENING_LINE in published.read_text(encoding="utf-8")
