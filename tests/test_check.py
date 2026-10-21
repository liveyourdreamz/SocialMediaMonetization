"""`cap check` runs the real-service seams; offline it must pass on a clean machine."""

from cap import check


def test_offline_checks_pass_on_a_clean_machine(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("CAP_MODEL_BACKEND=mock\n", encoding="utf-8")
    monkeypatch.setenv("CAP_DATA_DIR", str(tmp_path / "d"))
    lines: list[str] = []
    assert check.run(offline=True, out=lines.append) == 0
    assert not any(ln.strip().startswith("FAIL") for ln in lines)
    assert any("video render" in ln and "hash bound" in ln for ln in lines)


def test_a_key_without_the_anthropic_backend_is_caught(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("CAP_MODEL_BACKEND=mock\nCAP_ANTHROPIC_API_KEY=sk-x\n")
    monkeypatch.setenv("CAP_DATA_DIR", str(tmp_path / "d"))
    lines: list[str] = []
    assert check.run(offline=True, out=lines.append) == 1
    assert any("FAIL .env and key" in ln and "not anthropic" in ln for ln in lines)
