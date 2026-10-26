"""`cap smoke` is the loop the launchers run in CI; keep it passing and printing.

Here it runs on the test Postgres server; the launchers (run.sh, install.bat) run the
same loop with the app starting its own Postgres, as it does on the laptop."""

from pathlib import Path

from cap import smoke


def test_smoke_runs_the_whole_laptop_loop(tmp_path: Path, monkeypatch, pg_url):
    monkeypatch.setenv("CAP_DATABASE_URL", pg_url)
    lines: list[str] = []
    assert smoke.run(tmp_path / "s", out=lines.append) == 0, "\n".join(lines[-5:])
    assert any("all" in ln and "steps passed" in ln for ln in lines)
    assert not any(ln.strip().startswith("FAIL") for ln in lines)
