"""The 21 Oct security review's demonstrated findings, each as the attack that worked."""

import httpx
import pytest
from fastapi.testclient import TestClient

from cap.approval.app import Store, create_app
from cap.demo.seed import seed_store


@pytest.fixture
def lan(tmp_path, monkeypatch):
    """The app as the laptop serves it: dev sign-in on, a passcode in .env, and a
    client that is NOT this machine (another device on the Wi-Fi)."""
    from cap.config import get_settings

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAP_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("CAP_RENDERER", "text")
    monkeypatch.setenv("CAP_DEV_REVIEWER_HEADER", "true")
    monkeypatch.setenv("CAP_LOCAL_PASSCODE", "482913")
    get_settings.cache_clear()
    store = Store()
    seed_store(store)
    app = create_app(store)
    phone = TestClient(app, client=("192.168.1.50", 50000), follow_redirects=False)
    laptop = TestClient(app, follow_redirects=False)  # "testclient" counts as this machine
    yield store, phone, laptop
    get_settings.cache_clear()


def test_finding_1_a_device_on_the_wifi_needs_the_passcode(lan):
    store, phone, laptop = lan
    assert "Passcode" in phone.get("/login").text
    r = phone.post("/login", data={"name": "hemant"})
    assert r.status_code == 403 and "cap_reviewer" not in r.cookies
    r = phone.post("/login", data={"name": "hemant", "passcode": "000000"})
    assert r.status_code == 403
    r = phone.post("/login", data={"name": "hemant", "passcode": "482913"})
    assert r.status_code == 303 and r.cookies.get("cap_reviewer")
    # the laptop itself signs in with the name alone
    assert laptop.post("/login", data={"name": "hemant"}).status_code == 303


def test_finding_1_the_api_header_works_only_from_this_machine(lan):
    store, phone, laptop = lan
    assert phone.get("/api/queue", headers={"X-Reviewer": "hemant"}).status_code == 401
    assert laptop.get("/api/queue", headers={"X-Reviewer": "hemant"}).status_code == 200


def test_finding_1_first_run_writes_a_random_signing_key_and_passcode(tmp_path):
    from cap.setup import ensure_local_secrets, read_passcode

    env = tmp_path / ".env"
    env.write_text("CAP_MODEL_BACKEND=mock\n")
    ensure_local_secrets(env)
    text = env.read_text()
    key = [ln for ln in text.splitlines() if ln.startswith("CAP_APPROVAL_SIGNING_KEY=")][0].split(
        "="
    )[1]
    assert len(key) == 64 and key != "local-dev-only-not-a-secret"
    code = read_passcode(env)
    assert len(code) == 6 and code.isdigit()
    ensure_local_secrets(env)  # a second run keeps them
    assert env.read_text() == text


def test_finding_2_a_platform_error_never_shows_the_app_secret(tmp_path, monkeypatch):
    from cap import connections as conn
    from cap.config import get_settings

    monkeypatch.setenv("CAP_DATA_DIR", str(tmp_path / "d"))
    get_settings.cache_clear()
    secret = "f00dfacecafebeef0123456789abcdef"
    conn.set_credentials("meta", {"app_id": "1234567890", "app_secret": secret})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "Invalid verification code format."}})

    real = httpx.Client

    class Patched(real):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)

    monkeypatch.setattr(httpx, "Client", Patched)
    try:
        with pytest.raises(ConnectionError) as ei:
            conn.complete("meta", "the-code", "http://localhost:8000/cb")
        status = conn.status("meta")
        assert secret not in str(ei.value) and secret not in status["last_error"]
        assert status["last_error"] == "HTTP 400: Invalid verification code format."
        # and a raw exception carrying the secret is redacted too
        assert secret not in conn.safe_error(RuntimeError(f"url?client_secret={secret}"), "meta")
    finally:
        get_settings.cache_clear()


def test_finding_3_a_tour_post_never_reaches_the_configured_platform(tmp_path, monkeypatch):
    from cap.approval import token as tok
    from cap.approval.app import publish_now
    from cap.config import get_settings
    from cap.demo.seed import demo_post
    from cap.schema.post_record import PostStatus

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAP_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("CAP_RENDERER", "text")
    get_settings.cache_clear()
    uploads: list[str] = []

    class FakeYouTube:
        name = "youtube"

        def upload(self, record, asset):
            uploads.append(record.post_id)
            return "VIDEO123"

    store = Store()
    store.publisher = FakeYouTube()  # what CAP_PUBLISHER=youtube configures
    rec = demo_post()
    rec.flags.tour = True
    rec.status = PostStatus.APPROVED
    rec.approval.token = tok.issue(
        rec.post_id, rec.asset.sha256, "hemant", "approve", canary=False
    ).encode()
    store.put(rec)
    try:
        for chosen in ([], ["youtube_long"]):  # nothing ticked, or YouTube ticked
            rec.status = PostStatus.APPROVED
            assert publish_now(store, rec, chosen) is None
            assert uploads == [] and rec.publish.destinations == ["local"]
    finally:
        get_settings.cache_clear()


def test_finding_6_uploads_must_be_real_images_and_are_capped(lan):
    import io

    from PIL import Image

    store, phone, laptop = lan
    laptop.post("/login", data={"name": "hemant"})
    png = io.BytesIO()
    Image.new("RGB", (8, 8)).save(png, "PNG")
    files = [("media", (f"s{i}.png", png.getvalue(), "image/png")) for i in range(12)]
    files.append(("media", ("fake.png", b"<script>not an image</script>", "image/png")))
    r = laptop.post(
        "/operator/brief",
        data={"csrf": laptop.cookies.get("cap_csrf"), "channel": "A", "topic": "Batteries"},
        files=files,
    )
    assert r.status_code == 303
    rec = next(p for p in store.posts.values() if p.brief.topic == "Batteries")
    assert any("10 screenshot(s) attached" in h for h in rec.history)
