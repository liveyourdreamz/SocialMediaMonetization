"""The 21 Oct security review's demonstrated findings, each as the attack that worked."""

import httpx
import pytest
from fastapi.testclient import TestClient

from cap.demo.seed import seed_store
from cap.server import create_app
from cap.store import MemoryStore
from tests.conftest import SIGNIN, drain, laptop_wifi


@pytest.fixture
def lan(tmp_path, monkeypatch):
    """The app as `cap serve` runs it (CAP_ENV=local, so no test allowance), with
    each person's secret in the settings, the laptop's browser and the phone."""
    from cap.config import get_settings

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAP_ENV", "local")
    monkeypatch.setenv("CAP_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("CAP_RENDERER", "text")
    laptop_wifi(monkeypatch, "192.168.1.20")
    get_settings.cache_clear()
    store = MemoryStore()
    seed_store(store)
    app = create_app(store)
    phone = TestClient(
        app,
        base_url="http://192.168.1.20:8000",
        client=("192.168.1.50", 50000),
        follow_redirects=False,
    )
    laptop = TestClient(app, base_url="http://127.0.0.1:8000", follow_redirects=False)
    yield store, phone, laptop
    get_settings.cache_clear()


def sign_in(c: TestClient, secret: str):
    c.get("/login")
    return c.post("/login", data={"secret": secret, "csrf": c.cookies.get("cap_csrf", "")})


def test_finding_1_first_run_writes_a_random_signing_key_and_one_secret_each(tmp_path):
    from cap.setup import ensure_local_secrets, signin_secrets

    env = tmp_path / ".env"
    env.write_text("CAP_MODEL_BACKEND=mock\nCAP_LOCAL_PASSCODE=482913\n")
    ensure_local_secrets(env)
    text = env.read_text()
    key = [ln for ln in text.splitlines() if ln.startswith("CAP_APPROVAL_SIGNING_KEY=")][0].split(
        "="
    )[1]
    assert len(key) == 64 and key != "local-dev-only-not-a-secret"
    codes = signin_secrets(env)
    assert set(codes) >= {"hemant", "layla"} and codes["hemant"] != codes["layla"]
    assert all(len(c) >= 10 and c == c.lower() for c in codes.values())
    assert "CAP_LOCAL_PASSCODE" not in text  # the shared passcode is gone
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
    from cap.approval.app import finalise_and_issue, publish_now
    from cap.config import get_settings
    from cap.demo.seed import demo_post
    from cap.domain.card import record_verdict
    from cap.schema.post_record import Decision, PostStatus

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAP_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("CAP_RENDERER", "text")
    get_settings.cache_clear()
    uploads: list[str] = []

    class FakeYouTube:
        name = "youtube"

        def upload(self, release):
            uploads.append(release.post_id)
            return "VIDEO123"

    store = MemoryStore()
    rec = demo_post()
    rec.flags.tour = True
    store.put(rec)
    record_verdict(
        rec, Decision.APPROVE, reviewer="hemant", time_on_card_s=999,
        opening_line="x", spot_check_supported=True,
    )  # fmt: skip
    finalise_and_issue(store, rec, "hemant", acknowledged=True)
    drain(store, rec.post_id)
    rec = store.get(rec.post_id)
    try:
        for chosen in ([], ["youtube_long"]):  # nothing ticked, or YouTube ticked
            rec.status = PostStatus.APPROVED
            rec.publish.platform_post_ids.clear()
            assert publish_now(store, rec, chosen, publisher=FakeYouTube()) is None
            assert uploads == [] and rec.publish.destinations == ["local"]
            assert rec.status == PostStatus.PUBLISHED
    finally:
        get_settings.cache_clear()


def test_finding_6_uploads_must_be_real_images_and_are_capped(lan):
    import io

    from PIL import Image

    store, phone, laptop = lan
    assert sign_in(laptop, SIGNIN["hemant"]).status_code == 303
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
    rec = next(p for p in store.all() if p.brief.topic == "Batteries")
    assert any("10 screenshot(s) attached" in h for h in rec.history)


@pytest.mark.parametrize("encoding", ["gzip", "deflate", "identity"])
def test_compressed_pages_are_read_once_not_decoded_twice(monkeypatch, encoding):
    """22 Oct on Hemant's laptop: every real page failed with 'incorrect header check'.
    Sites send gzip; the capped reader decoded it and then copied the gzip header."""
    import gzip
    import zlib

    from cap import fetch

    html = b"<html><body><article>Silk is a protein fibre.</article></body></html>"
    packed = {"gzip": gzip.compress, "deflate": zlib.compress, "identity": lambda b: b}[encoding](
        html
    )

    def handler(req):
        return httpx.Response(
            200,
            headers={"content-type": "text/html", "content-encoding": encoding},
            content=packed,
        )

    monkeypatch.setattr(fetch, "check_public", lambda url: None)
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as c:
        text, _ = fetch.fetch_one(c, "https://example.org/silk")
    assert "Silk is a protein fibre." in text
