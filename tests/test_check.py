"""`cap check` runs the real-service seams; offline it must pass on a clean machine."""

from cap import check


def test_offline_checks_pass_on_a_clean_machine(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # no .env yet: the check creates it (stdin is not a terminal, so no prompt)
    monkeypatch.setenv("CAP_DATA_DIR", str(tmp_path / "d"))
    lines: list[str] = []
    assert check.run(offline=True, out=lines.append) == 0
    assert (tmp_path / ".env").exists()
    assert not any(ln.strip().startswith("FAIL") for ln in lines)
    assert any("video render" in ln and "hash bound" in ln for ln in lines)


def test_a_key_without_the_anthropic_backend_is_caught(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("CAP_MODEL_BACKEND=mock\nCAP_ANTHROPIC_API_KEY=sk-x\n")
    monkeypatch.setenv("CAP_DATA_DIR", str(tmp_path / "d"))
    lines: list[str] = []
    assert check.run(offline=True, out=lines.append) == 1
    assert any("FAIL .env and key" in ln and "not anthropic" in ln for ln in lines)


def test_the_console_is_switched_to_utf8_so_arabic_titles_print(monkeypatch, capsys):
    """Windows consoles are cp1252 by default; the first Arabic title printed by the
    pipeline would raise UnicodeEncodeError. The CLI reconfigures stdout first."""
    import io
    import sys

    from cap.cli import utf8_console

    buf = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", buf)
    utf8_console()
    print("تحلية مياه البحر")  # must not raise
    assert sys.stdout.encoding.lower() == "utf-8"


def test_a_real_brief_gets_its_channels_character_and_drafts_from_the_topic(tmp_path, monkeypatch):
    """With the real backend a brief must not be handed the demo's Notely sources and
    Patch's stub block: Falak gets Falak's block, and a brief with nothing to draft
    from parks with a reason that says what to add."""
    from cap.approval.app import Store
    from cap.config import get_settings
    from cap.demo import happy_fixtures
    from cap.demo.seed import run_brief
    from cap.models import MockClient
    from cap.schema.post_record import PostStatus

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAP_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("CAP_MODEL_BACKEND", "anthropic")
    monkeypatch.setenv("CAP_ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key")
    get_settings.cache_clear()
    seen: list[str] = []

    class Spy(MockClient):
        def _complete(self, call):
            seen.append("".join(b["text"] for b in call.system))
            return super()._complete(call)

    monkeypatch.setattr("cap.models.get_client", lambda fixtures=None: Spy(happy_fixtures()))
    monkeypatch.setattr(
        "cap.fetch.fetch_sources",
        lambda urls, timeout=20.0: [
            {
                "url": u,
                "body": "Seawater desalination uses reverse osmosis.",
                "fetched_at": "",
                "page_date": None,
            }
            for u in urls
        ],
    )
    try:
        store = Store()
        monkeypatch.setattr(
            "cap.research.find_sources",
            lambda topic, ch, claims=None: ([], ["research failed: no network"]),
        )
        # D41: no sources found is no longer a park; the draft goes ahead from knowledge
        rec = run_brief(store, "B", "desalination")
        assert rec.status != PostStatus.PARKED, rec.history[-1]
        assert any("research: looking for sources" in h for h in rec.history)
        rec = run_brief(store, "B", "desalination", urls=["https://example.org/desal"])
        assert "drafting_mode" in seen[0]  # the drafter is told to write from knowledge
        assert seen and 'character="Falak"' in seen[0] and "Notely" not in seen[0]
        assert (
            rec.brief.anchor_elements[:1] == ["from orbit"] and rec.brief.format_id == "from_orbit"
        )
    finally:
        get_settings.cache_clear()


def _public_names_only(monkeypatch):
    """The sandbox cannot resolve outside names; treat names as public, keep the real
    check for IP literals (what an attacker would use)."""
    import ipaddress

    from cap import fetch

    real = fetch.check_public

    def check(url):
        from urllib.parse import urlparse

        host = urlparse(url).hostname or ""
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if host in ("localhost",):
                return real(url)
            return None
        return real(url)

    monkeypatch.setattr(fetch, "check_public", check)


def test_fetch_failures_say_why_and_wikipedia_uses_its_rest_api(monkeypatch):
    _public_names_only(monkeypatch)
    import httpx

    from cap import fetch

    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), request.headers.get("user-agent", "")))
        u = str(request.url)
        if "rest_v1/page/html/Rainbow" in u:
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text="<html><body><p>A rainbow is an optical phenomenon.</p></body></html>",
            )
        if "blocks-scripts.example" in u:
            if "Mozilla" in request.headers.get("user-agent", ""):
                return httpx.Response(
                    200,
                    headers={"content-type": "text/html"},
                    text="<html><body><main>Served to browsers only.</main></body></html>",
                )
            return httpx.Response(403)
        if "gone.example" in u:
            return httpx.Response(404)
        raise httpx.ConnectError("name or service not known")

    real = httpx.Client

    class Patched(real):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)

    monkeypatch.setattr(httpx, "Client", Patched)
    docs = fetch.fetch_sources(
        [
            "https://en.wikipedia.org/wiki/Rainbow",
            "https://blocks-scripts.example/page",
            "https://gone.example/x",
            "https://nowhere.invalid/",
        ]
    )
    assert docs[0]["body"].startswith("A rainbow") and seen[0][0].endswith("/page/html/Rainbow")
    assert (
        docs[1]["body"] == "Served to browsers only."
        and "Mozilla" in seen[-3][1]
        or "Mozilla" in seen[2][1]
    )
    assert fetch.failed_reason(docs[2]) == "HTTP 404 from the site"
    assert fetch.failed_reason(docs[3]).startswith("could not connect")
    assert fetch.failed_reason(docs[0]) is None


def test_a_pinned_page_that_fails_is_reported_and_the_draft_still_runs(tmp_path, monkeypatch):
    from cap.approval.app import Store
    from cap.config import get_settings
    from cap.demo.seed import new_brief, run_brief
    from cap.schema.post_record import PostStatus

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAP_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("CAP_MODEL_BACKEND", "anthropic")
    monkeypatch.setenv("CAP_ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key")
    get_settings.cache_clear()
    from cap.demo import happy_fixtures
    from cap.models import MockClient

    monkeypatch.setenv("CAP_RENDERER", "text")
    get_settings.cache_clear()
    monkeypatch.setattr("cap.models.get_client", lambda fixtures=None: MockClient(happy_fixtures()))
    monkeypatch.setattr(
        "cap.research.find_sources",
        lambda topic, ch, claims=None: ([], ["research: nothing found"]),
    )
    monkeypatch.setattr(
        "cap.fetch.fetch_sources",
        lambda urls, timeout=20.0: [
            {
                "url": u,
                "body": "[fetch failed: HTTP 403 from the site]",
                "fetched_at": "",
                "page_date": None,
            }
            for u in urls
        ],
    )
    try:
        store = Store()
        rec = run_brief(store, "B", "Rainbows", urls=["https://x.example/a"])
        assert rec.status != PostStatus.PARKED, rec.history[-1]  # D41: the draft goes ahead
        assert any("could not fetch https://x.example/a: HTTP 403" in h for h in rec.history)
        b = new_brief("B", "Rainbows").brief
        assert (
            b.format_id == "from_orbit" and b.claims_allowed and "from orbit" in b.anchor_elements
        )
        assert new_brief("A", "x").brief.format_id == "take_it_apart"  # D42
    finally:
        get_settings.cache_clear()


def test_a_topic_alone_is_enough_the_system_finds_the_sources(tmp_path, monkeypatch):
    """The PRD's agent 1 finds the sources; Hemant files a topic and approves."""
    from cap import research
    from cap.approval.app import Store
    from cap.config import get_settings
    from cap.demo import happy_fixtures
    from cap.demo.seed import run_brief
    from cap.models import MockClient
    from cap.schema.post_record import PostStatus

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAP_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("CAP_RENDERER", "text")
    monkeypatch.setenv("CAP_MODEL_BACKEND", "anthropic")
    monkeypatch.setenv("CAP_ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key")
    get_settings.cache_clear()
    monkeypatch.setattr("cap.models.get_client", lambda fixtures=None: MockClient(happy_fixtures()))
    asked: list[tuple[str, str]] = []

    def scout(topic, channel, claims=None):
        asked.append((topic, channel))
        return [
            "https://www.nasa.gov/rainbows",
            "https://www.reddit.com/r/x",
            "https://en.wikipedia.org/wiki/Rainbow",
        ], "chose two"

    monkeypatch.setattr(research, "_search_with_model", scout)
    monkeypatch.setattr(
        "cap.fetch.fetch_sources",
        lambda urls, timeout=20.0: [
            {
                "url": u,
                "body": f"Text from {u}. Notes are stored locally first and then synced.",
                "fetched_at": "",
                "page_date": "2026-01-02",
            }
            for u in urls
        ],
    )
    try:
        store = Store()
        rec = run_brief(store, "B", "How rainbows form")
        assert asked == [("How rainbows form", "B")]
        assert rec.status != PostStatus.PARKED
        assert any("source: https://www.nasa.gov/rainbows (2026-01-02)" in h for h in rec.history)
        assert not any("reddit" in h for h in rec.history)  # UGC is never a source
    finally:
        get_settings.cache_clear()


def test_without_a_key_the_scout_uses_wikipedia_search(tmp_path, monkeypatch):
    _public_names_only(monkeypatch)
    import httpx

    from cap import research
    from cap.config import get_settings

    monkeypatch.chdir(tmp_path)  # never the repository's own .env
    monkeypatch.setenv("CAP_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("CAP_MODEL_BACKEND", "mock")
    get_settings.cache_clear()

    def handler(request: httpx.Request) -> httpx.Response:
        u = str(request.url)
        if "w/api.php" in u:
            return httpx.Response(
                200, json={"query": {"search": [{"title": "Rainbow"}, {"title": "Refraction"}]}}
            )
        if "rest_v1/page/html/" in u:
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text="<html><body><p>Light bends.</p></body></html>",
            )
        return httpx.Response(404)

    real = httpx.Client

    class Patched(real):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)

    monkeypatch.setattr(httpx, "Client", Patched)
    try:
        docs, log = research.find_sources("rainbows", "B")
        assert [d["url"] for d in docs] == [
            "https://en.wikipedia.org/wiki/Rainbow",
            "https://en.wikipedia.org/wiki/Refraction",
        ]
        assert docs[0]["body"] == "Light bends." and any(
            "Wikipedia search for 'rainbows'" in ln for ln in log
        )
    finally:
        get_settings.cache_clear()


def test_the_fetcher_refuses_internal_addresses_and_redirects_to_them(monkeypatch):
    """Review finding 4: a pinned or model-chosen URL could reach the laptop's own
    services, the router or a cloud metadata server, directly or by a redirect."""
    import httpx

    from cap import fetch

    _public_names_only(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "redirector.example":
            return httpx.Response(
                302, headers={"location": "http://169.254.169.254/latest/meta-data"}
            )
        if request.url.host == "big.example":
            return httpx.Response(
                200, headers={"content-type": "text/plain"}, content=b"x" * 5_000_000
            )
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="internal secret")

    real = httpx.Client

    class Patched(real):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)

    monkeypatch.setattr(httpx, "Client", Patched)
    docs = fetch.fetch_sources(
        [
            "http://127.0.0.1:8000/connections",
            "http://localhost/admin",
            "http://192.168.1.1/",
            "http://redirector.example/x",
            "http://big.example/huge",
        ]
    )
    reasons = [fetch.failed_reason(d) for d in docs]
    assert all(r and r.startswith("refused:") for r in reasons[:4]), reasons
    assert "internal secret" not in str(docs)
    assert fetch.failed_reason(docs[4]) is None and len(docs[4]["body"]) <= fetch.MAX_CHARS


def test_characters_check_names_the_channel_whose_block_the_critic_refuses(monkeypatch, tmp_path):
    """22 Oct: the real critic refused Falak (no speaking rate). The check must say which."""
    import pytest

    from cap import agents, check
    from cap.agents import AgentBlockedError
    from cap.config import get_settings

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAP_MODEL_BACKEND", "anthropic")
    monkeypatch.setenv("CAP_ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key")
    get_settings.cache_clear()
    seen: list[str] = []

    def fake(rec, client, block):
        seen.append(rec.channel_id.value)
        assert "words a minute" in block  # what P3 needs, in every block it is given
        if rec.channel_id.value == "B" and "140 words" not in block:
            raise AgentBlockedError("P3: no speaking rate")
        rec.draft.critic_verdict = "revise"
        return rec, False

    monkeypatch.setattr(agents, "critique", fake)
    out = check._characters()
    assert seen == ["A", "B", "C"] and "A revise" in out
    monkeypatch.setattr(
        agents, "critique", lambda *a: (_ for _ in ()).throw(AgentBlockedError("P3: no rate"))
    )
    with pytest.raises(RuntimeError, match="channel A: P3: no rate"):
        check._characters()
    get_settings.cache_clear()
