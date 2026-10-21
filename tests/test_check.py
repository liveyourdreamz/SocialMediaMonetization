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


def test_a_real_brief_gets_its_channels_character_and_parks_without_material(tmp_path, monkeypatch):
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
        rec = run_brief(store, "B", "desalination")
        assert rec.status == PostStatus.PARKED and "add one or more source URLs" in rec.history[-1]
        rec = run_brief(store, "B", "desalination", urls=["https://example.org/desal"])
        assert seen and 'character="Falak"' in seen[0] and "Notely" not in seen[0]
        assert rec.brief.anchor_elements[:1] == ["from orbit"] and rec.brief.format_id == "from_orbit"
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


def test_a_brief_whose_pages_all_failed_parks_with_the_reasons(tmp_path, monkeypatch):
    from cap.approval.app import Store
    from cap.config import get_settings
    from cap.demo.seed import new_brief, run_brief
    from cap.schema.post_record import PostStatus

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAP_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("CAP_MODEL_BACKEND", "anthropic")
    monkeypatch.setenv("CAP_ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key")
    get_settings.cache_clear()
    calls: list[str] = []
    monkeypatch.setattr(
        "cap.models.get_client", lambda fixtures=None: calls.append("model") or None
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
        assert rec.status == PostStatus.PARKED and calls == []
        assert (
            any("HTTP 403" in h for h in rec.history)
            and "fix or replace the links" in rec.history[-1]
        )
        b = new_brief("B", "Rainbows").brief
        assert (
            b.format_id == "from_orbit" and b.claims_allowed and "from orbit" in b.anchor_elements
        )
        assert new_brief("A", "x").brief.format_id == "tested_7_days"
    finally:
        get_settings.cache_clear()
