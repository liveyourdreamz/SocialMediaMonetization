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
        assert rec.brief.anchor_elements == ["from orbit"]
    finally:
        get_settings.cache_clear()
