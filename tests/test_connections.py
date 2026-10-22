"""Connections: credentials captured in the app, accounts connected by OAuth from the
browser, destinations chosen on the Outbox, results recorded per destination."""

import json
from pathlib import Path

import httpx
import pytest

from cap import connections as conn
from cap.approval import release_job
from cap.approval.app import FileStore
from cap.publisher import LocalFolderAdapter, Upload
from cap.schema.post_record import PostStatus
from tests.test_ui import Browser, create_app


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAP_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("CAP_RENDERER", "text")
    from cap.config import get_settings
    from cap.demo.seed import seed_store

    get_settings.cache_clear()
    store = FileStore(tmp_path / "d")
    seed_store(store, demo=True)
    api = Browser(create_app(store), follow_redirects=False)
    api.sign_in("hemant")
    yield store, api
    get_settings.cache_clear()


def test_credentials_are_captured_kept_private_and_never_echoed(world, tmp_path):
    store, api = world
    html = api.get("/connections").text
    assert "YouTube" in html and "Facebook Page and Instagram" in html and "TikTok" in html
    assert "/connections/youtube/callback" in html  # the redirect URI to register
    r = api.form(
        "/connections/youtube/credentials",
        client_id="123456789-abc.apps.googleusercontent.com",
        client_secret="GOCSPX-verysecretvalue",
    )
    assert r.status_code == 303
    html = api.get("/connections").text
    assert "GOCSPX-verysecretvalue" not in html and "123456789-abc" not in html
    assert "….com" in html and "credentials saved" in html
    f = tmp_path / "d" / "connections" / "youtube.json"
    assert f.exists()
    assert json.loads(f.read_text())["credentials"]["client_secret"] == "GOCSPX-verysecretvalue"
    # a blank field keeps the saved value
    api.form("/connections/youtube/credentials", client_id="", client_secret="")
    assert json.loads(f.read_text())["credentials"]["client_id"].startswith("123456789")
    # the Arabic reviewer cannot reach any of it
    api.sign_in("layla")
    assert api.get("/connections").status_code == 403


def test_connect_round_trip_stores_the_token_and_shows_the_account(world, monkeypatch):
    store, api = world
    api.form("/connections/youtube/credentials", client_id="cid.apps", client_secret="sec")
    r = api.get("/connections/youtube/connect")
    assert r.status_code == 303
    loc = r.headers["location"]
    assert (
        loc.startswith("https://accounts.google.com/o/oauth2/v2/auth?") and "youtube.upload" in loc
    )
    assert "client_id=cid.apps" in loc and "redirect_uri=http" in loc
    state = [p for p in loc.split("&") if p.startswith("state=")][0].split("=")[1]

    seen = {}

    def fake_exchange(platform, code, redirect_uri):
        seen["exchange"] = (platform, code, redirect_uri)
        return {
            "access_token": "ya29-ACCESS-TOKEN-VALUE",
            "refresh_token": "1//REFRESH-TOKEN",
            "expires_in": 3600,
        }

    def fake_account(platform, token):
        return {"name": "Patch Tests Tools", "id": "UC123"}

    monkeypatch.setattr(conn, "exchange", fake_exchange)
    monkeypatch.setattr(conn, "fetch_account", fake_account)
    # a callback that did not start here is refused
    r = api.get("/connections/youtube/callback?code=abc&state=wrong")
    assert r.status_code == 303 and "did%20not%20start" in r.headers["location"]
    r = api.get(f"/connections/youtube/callback?code=abc&state={state}")
    assert r.status_code == 303 and r.headers["location"] == "/connections?connected=youtube"
    assert seen["exchange"][0] == "youtube" and seen["exchange"][1] == "abc"
    html = api.get("/connections").text
    assert "Patch Tests Tools" in html and "connected" in html
    assert "ACCESS-TOKEN-VALUE" not in html and "REFRESH-TOKEN" not in html
    assert conn.connected_destinations() == ["local", "youtube_long", "youtube_short"]
    assert conn.youtube_credentials_json()["refresh_token"] == "1//REFRESH-TOKEN"
    # disconnect keeps the credentials, forget removes everything
    api.form("/connections/youtube/disconnect")
    assert (
        not conn.status("youtube")["connected"] and conn.status("youtube")["credentials_complete"]
    )
    api.form("/connections/youtube/disconnect", forget="1")
    assert not conn.status("youtube")["credentials"]


def test_outbox_offers_connected_destinations_and_records_each_result(world, monkeypatch):
    store, api = world
    store.publisher = None  # destinations decide
    post = next(p for p in store.queue() if p.channel_id.value == "A" and not p.flags.canary)
    api.get(f"/posts/{post.post_id}")
    api.form(
        f"/posts/{post.post_id}/verdict",
        decision="approve",
        opening_line="x",
        spot_check_supported="yes",
    )
    api.form(f"/posts/{post.post_id}/finalise")
    release_job.wait(post.post_id, 30)
    html = api.get(f"/posts/{post.post_id}/preview").text
    assert (
        "Where it goes" in html
        and "connect it first" in html
        and 'value="youtube_long" disabled' in html
    )

    # connect YouTube and TikTok (tokens straight into the store), then publish to both
    conn.save(
        "youtube",
        {
            "credentials": {"client_id": "c", "client_secret": "s"},
            "token": {"access_token": "a", "refresh_token": "r"},
            "account": {"name": "YT", "id": "1"},
        },
    )
    conn.save(
        "tiktok",
        {
            "credentials": {"client_key": "k", "client_secret": "s"},
            "token": {"access_token": "t"},
            "account": {"name": "TT", "id": "2"},
        },
    )
    html = api.get(f"/posts/{post.post_id}/preview").text
    assert 'value="youtube_long" checked' in html and 'value="tiktok" checked' in html

    class FakeYouTube:
        name = "youtube"

        def upload(self, release):
            return Upload("vid1", "https://www.youtube.com/watch?v=vid1")

    class FailingTikTok:
        name = "tiktok"

        def upload(self, release):
            raise RuntimeError("app not audited")

    import cap.publisher as pub

    monkeypatch.setattr(
        pub,
        "adapters_for",
        lambda chosen, local: [LocalFolderAdapter(local), FakeYouTube(), FailingTikTok()],
    )
    r = api.form(f"/posts/{post.post_id}/publish", destinations=["youtube_long", "tiktok"])
    assert r.status_code == 303 and "refused" not in r.headers["location"]
    rec = store.get(post.post_id)
    assert rec.status == PostStatus.PUBLISHED
    assert (
        rec.publish.platform_post_ids["youtube"] == "vid1"
        and "local" in rec.publish.platform_post_ids
    )
    assert "app not audited" in rec.publish.errors["tiktok"]
    assert rec.publish.destinations == ["local", "youtube_long", "tiktok"]
    html = api.get(f"/posts/{post.post_id}/preview").text
    assert (
        "Where it went" in html
        and "youtube.com/watch?v=vid1" in html
        and "not sent" in html
        and "app not audited" in html
    )


def test_a_tour_post_never_leaves_the_laptop(world, monkeypatch):
    store, api = world
    store.publisher = None
    conn.save(
        "youtube",
        {
            "credentials": {"client_id": "c", "client_secret": "s"},
            "token": {"access_token": "a"},
            "account": {"name": "YT", "id": "1"},
        },
    )
    import cap.publisher as pub

    called = []
    monkeypatch.setattr(
        pub,
        "adapters_for",
        lambda chosen, local: called.append(chosen) or [LocalFolderAdapter(local)],
    )
    from cap.demo.seed import demo_post

    rec = demo_post()
    rec.flags.tour = True
    store.put(rec)
    api.get(f"/posts/{rec.post_id}")
    api.form(
        f"/posts/{rec.post_id}/verdict",
        decision="approve",
        opening_line="x",
        spot_check_supported="yes",
    )
    api.form(f"/posts/{rec.post_id}/finalise")
    release_job.wait(rec.post_id, 30)
    html = api.get(f"/posts/{rec.post_id}/preview").text
    assert (
        "A tour post goes to the folder on this laptop only" in html
        and "never sent to YouTube" in html
    )
    api.form(f"/posts/{rec.post_id}/publish", destinations=["youtube_long"])
    assert called == [[]] and store.get(rec.post_id).status == PostStatus.PUBLISHED


def test_auth_urls_and_exchanges_for_each_platform(tmp_path, monkeypatch):
    monkeypatch.setenv("CAP_DATA_DIR", str(tmp_path / "d"))
    from cap.config import get_settings

    get_settings.cache_clear()
    try:
        conn.set_credentials("meta", {"app_id": "app1", "app_secret": "s1"})
        conn.set_credentials("tiktok", {"client_key": "key1", "client_secret": "s2"})
        u = conn.auth_url("meta", "http://localhost:8000/cb", "st")
        assert (
            u.startswith("https://www.facebook.com/")
            and "instagram_content_publish" in u
            and "client_id=app1" in u
        )
        u = conn.auth_url("tiktok", "http://localhost:8000/cb", "st")
        assert (
            u.startswith("https://www.tiktok.com/v2/auth/authorize/")
            and "client_key=key1" in u
            and "video.publish" in u
        )

        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            if "oauth/access_token" in str(request.url) and "fb_exchange_token" in str(request.url):
                return httpx.Response(200, json={"access_token": "long-lived"})
            if "oauth/access_token" in str(request.url):
                return httpx.Response(200, json={"access_token": "short"})
            if "me/accounts" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {
                                "id": "page1",
                                "name": "Falak Page",
                                "access_token": "pagetok",
                                "instagram_business_account": {"id": "ig1", "username": "falak"},
                            }
                        ]
                    },
                )
            if "tiktokapis.com/v2/oauth/token" in str(request.url):
                return httpx.Response(
                    200, json={"access_token": "tt", "refresh_token": "ttr", "open_id": "o1"}
                )
            if "user/info" in str(request.url):
                return httpx.Response(
                    200, json={"data": {"user": {"open_id": "o1", "display_name": "Falak"}}}
                )
            return httpx.Response(404)

        real_client = httpx.Client

        class Patched(real_client):
            def __init__(self, *a, **kw):
                kw["transport"] = httpx.MockTransport(handler)
                super().__init__(*a, **kw)

        monkeypatch.setattr(httpx, "Client", Patched)
        acct = conn.complete("meta", "code", "http://localhost:8000/cb")
        assert (
            acct["name"] == "Falak Page"
            and acct["page_token"] == "pagetok"
            and acct["instagram"] == "falak"
        )
        assert conn.connected_destinations() == ["local", "facebook", "instagram"]
        acct = conn.complete("tiktok", "code", "http://localhost:8000/cb")
        assert acct["name"] == "Falak" and "tiktok" in conn.connected_destinations()
        # the stored file holds the token but the status never does
        s = conn.status("meta")
        assert s["connected"] and "pagetok" not in json.dumps(
            {k: v for k, v in s.items() if k != "account"}
        )
        assert Path(tmp_path / "d" / "connections" / "meta.json").exists()
    finally:
        get_settings.cache_clear()
