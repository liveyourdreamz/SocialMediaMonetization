"""The guided tour: one worked Patch episode, every stop renders with that stage's
content, and the last stops are done for real on the tour post."""

import time

from cap.approval import release_job
from cap.approval.app import FileStore
from cap.publisher import LocalFolderAdapter
from cap.schema.post_record import PostStatus
from cap.ui.tour import STOPS
from tests.test_ui import Browser, create_app


def _wait_tour(store, timeout=180.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for p in store.posts.values():
            if p.flags.tour and p.status.value not in ("drafting", "gated", "rendering"):
                return p
        time.sleep(0.1)
    raise AssertionError("tour pipeline did not finish")


def test_the_tour_runs_idea_to_published_on_a_real_post(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAP_DATA_DIR", str(tmp_path / "cap-data"))
    monkeypatch.setenv("CAP_RENDERER", "video")
    monkeypatch.setenv("CAP_VOICE", "silent")
    from cap.config import get_settings

    get_settings.cache_clear()
    store = FileStore(tmp_path / "cap-data")
    store.publisher = LocalFolderAdapter(tmp_path / "cap-data" / "published")
    api = Browser(create_app(store), follow_redirects=False)
    api.sign_in("hemant")

    intro = api.get("/tour").text
    assert "Start the tour" in intro and all(s.title in intro for s in STOPS)
    assert "Guided tour" in api.get("/").text and "/tour" in api.get("/operator").text

    r = api.form("/tour/start")
    assert r.status_code == 303 and r.headers["location"].startswith("/tour/")
    post_id = r.headers["location"].split("/")[2]
    # the first stops render at once, while the pipeline is still running
    html = api.get(f"/tour/{post_id}/1").text
    assert "Driftpad" in html and "Stop 1" in html and "fictional" in html
    assert (
        "On day 5 the export failed twice" in api.get(f"/tour/{post_id}/2").text
        and "changelog" in api.get(f"/tour/{post_id}/2").text
    )
    assert "character_block" in api.get(f"/tour/{post_id}/3").text

    rec = _wait_tour(store)
    assert rec.post_id == post_id and rec.status == PostStatus.AWAITING_APPROVAL
    for n, stop in enumerate(STOPS, start=1):
        html = api.get(f"/tour/{post_id}/{n}").text
        assert stop.title in html and "refresh" not in html, n
    assert "[your opening line goes here]" in api.get(f"/tour/{post_id}/4").text
    assert (
        "10 claims" in api.get(f"/tour/{post_id}/5").text
        and "2.4.1" in api.get(f"/tour/{post_id}/5").text
    )
    html6 = api.get(f"/tour/{post_id}/6").text
    assert "revise" in html6 and "Verdict first: Watch." in html6 and "revised once" in html6
    assert "youtube_short" in api.get(f"/tour/{post_id}/7").text
    assert "Judge A" in api.get(f"/tour/{post_id}/8").text
    assert rec.asset.sha256 in api.get(f"/tour/{post_id}/9").text
    assert "Open Connections" in api.get(f"/tour/{post_id}/10").text
    assert f"/posts/{post_id}" in api.get(f"/tour/{post_id}/11").text
    assert "No verdict yet" in api.get(f"/tour/{post_id}/12").text
    assert "No token yet" in api.get(f"/tour/{post_id}/13").text

    # the real card, the real verdict, the real outbox, the real publish
    api.get(f"/posts/{post_id}")
    api.form(
        f"/posts/{post_id}/verdict",
        decision="approve",
        opening_line="Seven days with Driftpad.",
        spot_check_supported="yes",
    )
    assert api.form(f"/posts/{post_id}/finalise").status_code == 303
    release_job.wait(post_id, 300)
    html = api.get(f"/tour/{post_id}/12").text
    assert "approve" in html and "Seven days with Driftpad." in html
    assert "Open the Outbox preview" in api.get(f"/tour/{post_id}/13").text
    assert api.form(f"/posts/{post_id}/publish").status_code == 303
    html = api.get(f"/tour/{post_id}/14").text
    assert (
        "End of the tour" in html
        and "written" in html
        and store.get(post_id).status == PostStatus.PUBLISHED
    )
    # a tour post is never counted as a real episode's data
    assert store.get(post_id).flags.tour


def test_a_non_tour_post_has_no_tour_pages(tmp_path, monkeypatch):
    from cap.approval.app import Store
    from cap.demo.seed import seed_store

    monkeypatch.chdir(tmp_path)
    store = Store()
    seed_store(store)
    api = Browser(create_app(store), follow_redirects=False)
    api.sign_in("hemant")
    pid = next(iter(store.posts))
    assert api.get(f"/tour/{pid}/1").status_code == 404


def test_the_card_shows_the_video_being_approved(tmp_path, monkeypatch):
    """The card is where the post is judged; the rendered video is what is published,
    so it is on the card, above the script, for Hemant and for the Arabic reviewer."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAP_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("CAP_RENDERER", "video")
    monkeypatch.setenv("CAP_VOICE", "silent")
    from cap.approval.app import Store
    from cap.config import get_settings
    from cap.demo.seed import seed_store

    get_settings.cache_clear()
    store = Store()
    seed_store(store, demo=True)
    api = Browser(create_app(store), follow_redirects=False)
    api.sign_in("hemant")
    post = next(p for p in store.queue() if p.channel_id.value == "A")
    html = api.get(f"/posts/{post.post_id}").text
    assert f"/assets/{post.post_id}/long.mp4" in html and "<video" in html
    assert "The video you are approving" in html
    # the file itself is served to the signed-in reviewer
    r = api.get(f"/assets/{post.post_id}/long.mp4")
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4"
    # Layla's stage-one card carries it too, labelled in Arabic
    api.sign_in("layla")
    c = next(p for p in store.posts.values() if p.channel_id.value == "C")
    html = api.get(f"/posts/{c.post_id}").text
    assert f"/assets/{c.post_id}/long.mp4" in html and "الفيديو الذي توافق عليه" in html


def test_screenshots_uploaded_with_a_brief_are_kept_for_the_renderer(tmp_path, monkeypatch):
    import io

    from PIL import Image

    from cap.approval.app import Store
    from cap.config import get_settings

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAP_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("CAP_RENDERER", "text")
    get_settings.cache_clear()
    try:
        store = Store()
        api = Browser(create_app(store), follow_redirects=False)
        api.sign_in("hemant")
        buf = io.BytesIO()
        Image.new("RGB", (200, 100), (10, 20, 30)).save(buf, "PNG")
        r = api.post(
            "/operator/brief",
            data={
                "csrf": api.cookies.get("cap_csrf"),
                "channel": "A",
                "topic": "Shots",
                "urls": "",
                "test_log": "Day 1: ok",
            },
            files=[
                ("media", ("day3.png", buf.getvalue(), "image/png")),
                ("media", ("notes.txt", b"x", "text/plain")),
            ],
        )
        assert r.status_code == 303
        rec = next(p for p in store.posts.values() if p.brief.topic == "Shots")
        folder = tmp_path / "d" / "media" / rec.post_id
        assert sorted(p.name for p in folder.iterdir()) == ["00-day3.png"]
        assert any("1 screenshot(s) attached" in h for h in rec.history)
    finally:
        get_settings.cache_clear()
