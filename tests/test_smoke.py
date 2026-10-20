"""`cap smoke` is the loop the launchers run in CI; keep it passing and printing."""

from pathlib import Path

from cap import smoke


def test_smoke_runs_the_whole_laptop_loop(tmp_path: Path, monkeypatch):
    lines: list[str] = []
    assert smoke.run(tmp_path / "s", out=lines.append) == 0
    assert any("all" in ln and "steps passed" in ln for ln in lines)
    assert not any(ln.strip().startswith("FAIL") for ln in lines)
