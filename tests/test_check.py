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


def test_fetch_failures_say_why_and_wikipedia_uses_its_rest_api(monkeypatch):
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
