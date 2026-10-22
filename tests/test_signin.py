"""Batch 1 security (22 Oct review): sign-in, sessions and the Host header.

C4: a web page on the laptop (DNS rebinding) must not reach the app; loopback is not
a credential; X-Reviewer is never honoured by `cap serve`.
H4: one secret per person, the role from the secret, lockout, revocable sessions,
CSRF on the sign-in form, API docs and crash details behind sign-in.
Every test drives the app as `cap serve` runs it (CAP_ENV=local, no test allowance).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from cap.approval.app import Store, create_app
from cap.demo.seed import seed_store
from cap.schema.post_record import Channel
from tests.conftest import SIGNIN, laptop_wifi

LAPTOP = "http://127.0.0.1:8000"
PHONE_HOST = "http://192.168.1.20:8000"  # the laptop's Wi-Fi address, as the phone types it


@pytest.fixture
def served(tmp_path, monkeypatch):
    from cap.config import get_settings

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAP_ENV", "local")  # as `cap serve`; CAP_TEST_CLIENT stays set
    monkeypatch.setenv("CAP_DATA_DIR", str(tmp_path / "d"))
    laptop_wifi(monkeypatch, "192.168.1.20")
    get_settings.cache_clear()
    store = Store()
    seed_store(store, demo=True)
    app = create_app(store)

    def client(base: str = LAPTOP, ip: str = "127.0.0.1") -> TestClient:
        return TestClient(app, base_url=base, client=(ip, 50000), follow_redirects=False)

    yield store, client
    get_settings.cache_clear()


def sign_in(c: TestClient, secret: str, **extra: str):
    c.get("/login")
    data = {"secret": secret, "csrf": c.cookies.get("cap_csrf", ""), **extra}
    return c.post("/login", data=data)


# ---------------------------------------------------------------- C4: Host header


def test_c4_a_foreign_host_header_is_refused_before_any_route(served):
    store, client = served
    rebind = client("http://attacker.example:8000")
    assert rebind.get("/login").status_code == 400
    assert sign_in(rebind, SIGNIN["hemant"]).status_code == 400
    assert rebind.get("/api/queue", headers={"X-Reviewer": "hemant"}).status_code == 400
    assert rebind.post("/connections/youtube/credentials", data={}).status_code == 400
    # this machine's own names and its Wi-Fi address still work
    for base in (LAPTOP, "http://localhost:8000", "http://[::1]:8000", PHONE_HOST):
        assert client(base).get("/login").status_code == 200, base


def test_c4_the_public_base_url_host_is_allowed(served, monkeypatch):
    from cap.config import get_settings

    store, _ = served
    monkeypatch.setenv("CAP_PUBLIC_BASE_URL", "https://cap.example.org")
    get_settings.cache_clear()
    app = create_app(store)
    assert TestClient(app, base_url="https://cap.example.org").get("/login").status_code == 200
    assert TestClient(app, base_url="https://other.example.org").get("/login").status_code == 400


def test_c4_the_testclient_hosts_are_a_test_only_allowance(served):
    store, client = served
    assert client("http://testserver").get("/login").status_code == 400


def test_c4_loopback_is_not_a_credential(served):
    store, client = served
    laptop = client()
    laptop.get("/login")
    r = laptop.post("/login", data={"name": "hemant", "csrf": laptop.cookies.get("cap_csrf")})
    assert r.status_code == 403 and "cap_reviewer" not in r.cookies
    assert laptop.get("/connections").status_code == 303  # still signed out


def test_c4_x_reviewer_is_never_honoured_by_cap_serve(served):
    store, client = served
    assert client().get("/api/queue", headers={"X-Reviewer": "hemant"}).status_code == 401


# ---------------------------------------------------------------- H4: secrets


def test_h4_the_role_comes_from_the_secret_never_from_a_typed_name(served):
    store, client = served
    layla = client(PHONE_HOST, "192.168.1.51")
    assert sign_in(layla, SIGNIN["layla"], name="hemant").status_code == 303
    assert layla.get("/connections").status_code == 403  # an Arabic reviewer, not hemant
    assert layla.get("/").headers["location"] == "/queue"
    hemant = client(PHONE_HOST, "192.168.1.50")
    assert sign_in(hemant, SIGNIN["hemant"], name="layla").status_code == 303
    assert hemant.get("/connections").status_code == 200


def test_h4_secrets_tolerate_phone_keyboards(served):
    store, client = served
    typed = " " + SIGNIN["hemant"].upper().replace("-", " ") + " "
    assert sign_in(client(), typed).status_code == 303


def test_i1_a_lan_device_with_no_secret_configured_is_refused(served, monkeypatch):
    from cap.config import get_settings

    store, client = served
    for configured in ("{}", '{"hemant": ""}'):
        monkeypatch.setenv("CAP_SIGNIN_SECRETS", configured)
        get_settings.cache_clear()
        for i, secret in enumerate(("", SIGNIN["hemant"], "anything-at-all")):
            phone = client(PHONE_HOST, f"192.168.1.{50 + i}")
            r = sign_in(phone, secret, name="hemant", passcode=secret)
            assert r.status_code == 403 and "cap_reviewer" not in r.cookies, (configured, secret)


def test_h4_a_short_configured_secret_never_matches(served, monkeypatch):
    from cap.config import get_settings

    store, client = served
    monkeypatch.setenv("CAP_SIGNIN_SECRETS", '{"hemant": "123456"}')
    get_settings.cache_clear()
    r = sign_in(client(), "123456", name="hemant", passcode="123456")
    assert r.status_code == 403 and "cap_reviewer" not in r.cookies


def test_h4_five_wrong_secrets_lock_the_device_out_even_for_the_right_one(served):
    store, client = served
    phone = client(PHONE_HOST, "192.168.1.50")
    codes = [sign_in(phone, f"wrong-guess-{i:04d}").status_code for i in range(5)]
    assert codes == [403] * 5
    r = sign_in(phone, SIGNIN["hemant"])
    assert r.status_code == 429 and "Wait 15 minutes" in r.text and "cap_reviewer" not in r.cookies
    # another device is not locked out
    assert sign_in(client(PHONE_HOST, "192.168.1.51"), SIGNIN["layla"]).status_code == 303


def test_h4_twenty_wrong_secrets_from_anywhere_pause_sign_in(served):
    store, client = served
    for i in range(20):
        ip = f"192.168.1.{100 + i // 4}"  # four tries per address, under the per-device limit
        assert sign_in(client(PHONE_HOST, ip), f"wrong-guess-{i:04d}").status_code in (403, 429)
    r = sign_in(client(PHONE_HOST, "192.168.1.60"), SIGNIN["hemant"])
    assert r.status_code == 429 and "paused for 15 minutes" in r.text
    assert sign_in(client(), SIGNIN["hemant"]).status_code == 429  # the laptop too


def test_the_global_pause_lifts_on_its_own_so_nobody_can_lock_the_owner_out():
    """Re-verification 22 Oct: a permanent freeze let anyone on the Wi-Fi lock the
    owner out until a restart. The pause is timed."""
    from cap.approval import throttle

    now = [1000.0]
    t = throttle.SignInThrottle(clock=lambda: now[0])
    for i in range(throttle.GLOBAL):
        t.failed(f"10.0.0.{i}")
    assert t.refusal("laptop") == throttle.FROZEN and t.frozen
    now[0] += throttle.GLOBAL_COOLDOWN_S + 1
    assert t.refusal("laptop") is None and not t.frozen


def test_h4_lockout_lifts_after_the_window(monkeypatch):
    from cap.approval import throttle

    now = [1000.0]
    t = throttle.SignInThrottle(clock=lambda: now[0])
    for _ in range(throttle.PER_CLIENT):
        t.failed("phone")
    assert t.refusal("phone") == throttle.LOCKED
    now[0] += throttle.CLIENT_WINDOW_S + 1
    assert t.refusal("phone") is None


def test_h4_the_sign_in_form_needs_its_csrf_token(served):
    store, client = served
    c = client()
    c.get("/login")
    r = c.post("/login", data={"secret": SIGNIN["hemant"], "name": "hemant"})
    assert r.status_code == 403 and "cap_reviewer" not in r.cookies
    r = c.post("/login", data={"secret": SIGNIN["hemant"], "name": "hemant", "csrf": "forged"})
    assert r.status_code == 403 and "cap_reviewer" not in r.cookies


def test_h4_logout_revokes_the_session_on_the_server(served):
    store, client = served
    c = client()
    assert sign_in(c, SIGNIN["hemant"]).status_code == 303
    stolen = c.cookies.get("cap_reviewer")
    assert c.post("/logout", data={"csrf": c.cookies.get("cap_csrf")}).status_code == 303
    replay = client()
    replay.cookies.set("cap_reviewer", stolen)
    assert replay.get("/operator").status_code == 303  # back to sign-in


def test_h4_a_session_ends_after_about_eight_hours():
    from cap.approval import sessions

    now = [0.0]
    store = sessions.SessionStore(clock=lambda: now[0])
    sid = store.start("hemant")
    now[0] = 7.9 * 3600
    assert store.name_for(sid) == "hemant"
    now[0] = 8 * 3600 + 1
    assert store.name_for(sid) is None


def test_h4_a_forged_or_planted_session_id_is_not_a_session(served):
    store, client = served
    c = client()
    c.cookies.set("cap_reviewer", "hemant")
    assert c.get("/operator").status_code == 303
    planted = "planted-session-id"
    c = client()
    c.cookies.set("cap_reviewer", planted)
    r = sign_in(c, SIGNIN["hemant"])
    assert r.status_code == 303 and r.cookies.get("cap_reviewer") not in (None, planted)


def test_h4_api_docs_need_an_approver(served):
    store, client = served
    anon = client()
    assert anon.get("/api/docs").status_code == 401
    assert anon.get("/api/openapi.json").status_code == 401
    layla = client(PHONE_HOST, "192.168.1.51")
    sign_in(layla, SIGNIN["layla"])
    assert layla.get("/api/openapi.json").status_code == 403
    hemant = client()
    sign_in(hemant, SIGNIN["hemant"])
    assert hemant.get("/api/openapi.json").status_code == 200
    assert hemant.get("/api/docs").status_code == 200


def test_h4_crash_details_only_for_a_signed_in_reviewer(served):
    store, client = served
    app = client().app

    def boom() -> None:
        raise RuntimeError("secret-internal-detail")

    app.add_api_route("/boom", boom)

    def browser() -> TestClient:
        return TestClient(app, base_url=LAPTOP, raise_server_exceptions=False)

    anon = browser()
    r = anon.get("/boom")
    assert r.status_code == 500 and "secret-internal-detail" not in r.text
    hemant = browser()
    sign_in(hemant, SIGNIN["hemant"])
    r = hemant.get("/boom")
    assert r.status_code == 500 and "secret-internal-detail" in r.text


def test_h4_arabic_reviewer_still_sees_channel_c_only(served):
    store, client = served
    layla = client(PHONE_HOST, "192.168.1.51")
    sign_in(layla, SIGNIN["layla"])
    a = next(p for p in store.posts.values() if p.channel_id == Channel.A)
    assert layla.get(f"/posts/{a.post_id}").status_code == 403


def test_c4_a_new_wifi_address_is_picked_up_and_names_never_are():
    from cap.approval.hosts import LAN_RECHECK_S, HostGuard, host_of

    lan = [frozenset({"192.168.1.20"})]
    now = [0.0]
    guard = HostGuard(None, {"localhost"}, lan=lambda: lan[0], clock=lambda: now[0])  # type: ignore[arg-type]
    assert guard.allows("192.168.1.20:8000") and not guard.allows("10.0.0.7:8000")
    lan[0] = frozenset({"10.0.0.7"})
    now[0] += LAN_RECHECK_S
    assert guard.allows("10.0.0.7:8000") and not guard.allows("evil.example")
    assert host_of("[::1]:8000") == "::1" and host_of("LOCALHOST.:8000") == "localhost"
    assert not guard.allows("") and not guard.allows("localhost:8000:1")
