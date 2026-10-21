"""The guided tour: one worked Patch episode, every stop renders with that stage's
content, and the last stops are done for real on the tour post."""

import time

from cap.approval.app import FileStore
from cap.publisher import LocalFolderAdapter
from cap.schema.post_record import PostStatus
from cap.ui.tour import STOPS
from tests.test_ui import Browser, create_app


def _wait_tour(store, timeout=60.0):
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
    assert "Notely 7-day test" in html and "Stop 1" in html
    assert "Day 3: sync failed twice" in api.get(f"/tour/{post_id}/2").text
    assert "character_block" in api.get(f"/tour/{post_id}/3").text

    rec = _wait_tour(store)
    assert rec.post_id == post_id and rec.status == PostStatus.AWAITING_APPROVAL
    for n, stop in enumerate(STOPS, start=1):
        html = api.get(f"/tour/{post_id}/{n}").text
        assert stop.title in html and "refresh" not in html, n
    assert "[your opening line goes here]" in api.get(f"/tour/{post_id}/4").text
    assert "5 claims" in api.get(f"/tour/{post_id}/5").text
    assert "went through first time" in api.get(f"/tour/{post_id}/6").text
    assert "youtube_short" in api.get(f"/tour/{post_id}/7").text
    assert "Judge A" in api.get(f"/tour/{post_id}/8").text
    assert rec.asset.sha256 in api.get(f"/tour/{post_id}/9").text
    assert f"/posts/{post_id}" in api.get(f"/tour/{post_id}/10").text
    assert "No verdict yet" in api.get(f"/tour/{post_id}/11").text
    assert "No token yet" in api.get(f"/tour/{post_id}/12").text

    # the real card, the real verdict, the real outbox, the real publish
    api.get(f"/posts/{post_id}")
    api.form(
        f"/posts/{post_id}/verdict",
        decision="approve",
        opening_line="Seven days with Notely.",
        spot_check_supported="yes",
    )
    assert api.form(f"/posts/{post_id}/finalise").status_code == 303
    html = api.get(f"/tour/{post_id}/11").text
    assert "approve" in html and "Seven days with Notely." in html
    assert "Open the Outbox preview" in api.get(f"/tour/{post_id}/12").text
    assert api.form(f"/posts/{post_id}/publish").status_code == 303
    html = api.get(f"/tour/{post_id}/13").text
    assert "End of the tour" in html and store.get(post_id).status == PostStatus.PUBLISHED
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
