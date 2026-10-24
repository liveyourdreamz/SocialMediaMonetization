"""Review H7: updating the laptop install is best effort and never stops the app.

The update logic lives in cap.updater (Python, so it runs here and in the Windows CI
job); install.bat only fetches and runs it. These tests drive it against a real local
"GitHub" (a bare repository) with every failure the review found: no network, a stale
.git/index.lock, files Hemant saved himself, commits made on the laptop, the app
already running. The last group pins the batch files' bytes and the rules cmd needs.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from cap import cli, updater

ROOT = Path(__file__).resolve().parents[1]
BRANCH = "bootstrap"


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


@pytest.fixture(autouse=True)
def _git_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(var, "test")
    for var in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(var, "test@example.com")
    monkeypatch.delenv("CAP_DATA_DIR", raising=False)
    monkeypatch.setattr(updater, "DEFAULT_PORT", _free_port())  # never a real app's port


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class World:
    """A bare 'GitHub' repo, a publisher clone that pushes to it, and the laptop clone."""

    def __init__(self, tmp: Path) -> None:
        self.origin = tmp / "origin.git"
        self.dev = tmp / "dev"
        self.laptop = tmp / "laptop"
        subprocess.run(["git", "init", "-q", "--bare", "-b", BRANCH, str(self.origin)], check=True)
        subprocess.run(["git", "clone", "-q", str(self.origin), str(self.dev)], check=True)
        _git(self.dev, "checkout", "-q", "-b", BRANCH)
        self.commit(
            {"youtube-test.bat": "v1\r\n", "README.md": "one\n", ".gitignore": "cap-data/\n"}
        )
        subprocess.run(
            ["git", "clone", "-q", "--branch", BRANCH, str(self.origin), str(self.laptop)],
            check=True,
        )

    def commit(self, files: dict[str, str], message: str = "change") -> str:
        for rel, text in files.items():
            (self.dev / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.dev / rel).write_bytes(text.encode())
        _git(self.dev, "add", "-A")
        _git(self.dev, "commit", "-q", "-m", message)
        _git(self.dev, "push", "-q", "origin", BRANCH)
        return _git(self.dev, "rev-parse", "HEAD")

    def head(self) -> str:
        return _git(self.laptop, "rev-parse", "HEAD")

    def run(self) -> tuple[int, str]:
        said: list[str] = []
        code = updater.main(["--dir", str(self.laptop), "--branch", BRANCH], out=said.append)
        return code, "\n".join(said)


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def test_up_to_date_and_then_updated(world: World) -> None:
    code, said = world.run()
    assert code == updater.OK and "up to date" in said
    new = world.commit({"README.md": "two\n"})
    code, said = world.run()
    assert code == updater.OK and "Code updated" in said
    assert world.head() == new and (world.laptop / "README.md").read_text() == "two\n"


def test_no_network_warns_once_and_leaves_the_code_alone(world: World) -> None:
    before = world.head()
    _git(world.laptop, "remote", "set-url", "origin", str(world.laptop.parent / "missing.git"))
    code, said = world.run()
    assert code == updater.NOT_UPDATED
    assert said.count("WARNING") == 1 and "already on this laptop" in said
    assert world.head() == before


def test_a_stale_index_lock_is_removed(world: World) -> None:
    new = world.commit({"README.md": "two\n"})
    lock = world.laptop / ".git" / "index.lock"
    lock.write_text("")
    old = time.time() - 30 * 60
    os.utime(lock, (old, old))
    code, said = world.run()
    assert code == updater.OK and "stale .git/index.lock" in said
    assert not lock.exists() and world.head() == new


def test_a_fresh_index_lock_is_left_for_the_git_that_owns_it(world: World) -> None:
    world.commit({"README.md": "two\n"})
    lock = world.laptop / ".git" / "index.lock"
    lock.write_text("")
    code, said = world.run()
    assert code == updater.NOT_UPDATED and "another git program" in said
    assert lock.exists()


def test_his_changes_in_the_way_are_backed_up_then_updated(world: World) -> None:
    # he saved his own copy of youtube-test.bat over the tracked one (review H7)
    (world.laptop / "youtube-test.bat").write_text("his own copy\n")
    new = world.commit({"youtube-test.bat": "v2\r\n"})
    code, said = world.run()
    assert code == updater.OK, said
    assert world.head() == new
    assert (world.laptop / "youtube-test.bat").read_bytes() == b"v2\r\n"
    backups = list((world.laptop / "cap-data" / updater.BACKUPS).glob("*/youtube-test.bat"))
    assert len(backups) == 1 and backups[0].read_text() == "his own copy\n"
    assert "youtube-test.bat" in said and str(backups[0].parent) in said
    assert _git(world.laptop, "status", "--porcelain") == ""


def test_an_untracked_file_where_the_update_brings_one_is_backed_up(world: World) -> None:
    (world.laptop / "full-run.bat").write_text("downloaded by hand\n")
    world.commit({"full-run.bat": "tracked now\r\n"})
    code, said = world.run()
    assert code == updater.OK, said
    assert (world.laptop / "full-run.bat").read_bytes() == b"tracked now\r\n"
    saved = next((world.laptop / "cap-data" / updater.BACKUPS).glob("*/full-run.bat"))
    assert saved.read_text() == "downloaded by hand\n"


def test_his_changes_the_update_does_not_touch_stay_where_they_are(world: World) -> None:
    (world.laptop / "README.md").write_text("my notes\n")
    world.commit({"youtube-test.bat": "v2\r\n"})
    code, said = world.run()
    assert code == updater.OK and "in the way" not in said
    assert (world.laptop / "README.md").read_text() == "my notes\n"
    assert not (world.laptop / "cap-data").exists()


def test_commits_made_on_the_laptop_are_kept_on_a_branch(world: World) -> None:
    (world.laptop / "notes.txt").write_text("mine\n")
    _git(world.laptop, "add", "notes.txt")
    _git(world.laptop, "commit", "-q", "-m", "local")
    local = world.head()
    new = world.commit({"README.md": "two\n"})
    code, said = world.run()
    assert code == updater.OK and world.head() == new
    branches = _git(world.laptop, "branch", "--list", "backup/*", "--format=%(objectname)")
    assert local in branches.split() and "backup/" in said


def test_backups_go_to_the_configured_data_dir(world: World) -> None:
    (world.laptop / ".env").write_text("﻿CAP_DATA_DIR=records\n", encoding="utf-8")
    (world.laptop / "youtube-test.bat").write_text("his\n")
    world.commit({"youtube-test.bat": "v2\r\n"})
    assert world.run()[0] == updater.OK
    assert list((world.laptop / "records" / updater.BACKUPS).glob("*/youtube-test.bat"))


def test_the_app_already_running_is_not_updated_under_it(world: World) -> None:
    before = world.head()
    world.commit({"README.md": "two\n"})
    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen()
        port = srv.getsockname()[1]
        (world.laptop / "cap-data").mkdir()
        (world.laptop / "cap-data" / updater.LOCK_FILE).write_text(f"port={port}\npid=1\n")
        code, said = world.run()
    assert code == updater.RUNNING and str(port) in said
    assert world.head() == before


def test_git_never_prompts_and_never_hangs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    def fake_run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        seen.update(kw, cmd=cmd)
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(updater.subprocess, "run", fake_run)
    with pytest.raises(updater.UpdateError, match="longer than"):
        updater._git(tmp_path, "fetch", timeout=1)
    env = seen["env"]
    assert isinstance(env, dict)
    assert env["GIT_TERMINAL_PROMPT"] == "0" and env["GCM_INTERACTIVE"] == "never"
    assert seen["stdin"] == subprocess.DEVNULL and seen["timeout"] == 1
    assert "credential.interactive=never" in seen["cmd"]  # type: ignore[operator]


def test_a_sign_in_github_wants_is_named_plainly() -> None:
    msg = updater._reason("fetch", "fatal: could not read Username for 'https://github.com'")
    assert "sign in" in msg and "git fetch" in msg


def test_runs_standalone_from_outside_the_package(world: World, tmp_path: Path) -> None:
    """install.bat runs the file straight out of the fetched commit with `python -I`."""
    copy = tmp_path / "elsewhere" / "cap-updater.py"
    copy.parent.mkdir()
    shutil.copy(ROOT / "src" / "cap" / "updater.py", copy)
    new = world.commit({"README.md": "two\n"})
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    done = subprocess.run(
        [sys.executable, "-I", str(copy), "--dir", str(world.laptop), "--branch", BRANCH],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert world.head() == new


def test_cap_update_command(world: World, monkeypatch: pytest.MonkeyPatch, capsys) -> None:  # type: ignore[no-untyped-def]
    new = world.commit({"README.md": "two\n"})
    assert cli.main(["update", "--dir", str(world.laptop), "--branch", BRANCH]) == 0
    assert world.head() == new and "Code updated" in capsys.readouterr().out


# ---- the batch files -------------------------------------------------------------------


def _bats() -> list[Path]:
    out = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "*.bat"], capture_output=True, text=True, check=True
    )
    return [ROOT / p for p in out.stdout.split()]


def test_every_batch_file_is_crlf_bytes_and_never_converted() -> None:
    """install.bat is downloaded raw from GitHub: the committed bytes are what cmd reads."""
    bats = _bats()
    assert {p.name for p in bats} >= {"install.bat", "youtube-test.bat", "start.bat"}
    for p in bats:
        data = p.read_bytes()
        assert b"\r\n" in data and data.count(b"\n") == data.count(b"\r\n"), p.name
        blob = subprocess.run(
            ["git", "-C", str(ROOT), "show", f":{p.relative_to(ROOT).as_posix()}"],
            capture_output=True,
            check=True,
        ).stdout
        assert blob.count(b"\n") == blob.count(b"\r\n"), f"{p.name} is stored with LF"
        attr = _git(ROOT, "check-attr", "text", "--", p.relative_to(ROOT).as_posix())
        assert attr.endswith(": unset"), attr


def test_install_bat_runs_from_a_copy_it_never_rereads() -> None:
    lines = (ROOT / "install.bat").read_text().splitlines()
    relaunch = [ln for ln in lines if "--from-temp" in ln and "call" in ln]
    assert len(relaunch) == 1 and relaunch[0].rstrip().endswith("& exit /b)")
    assert lines.index(relaunch[0]) < lines.index(":main")


def test_install_bat_updates_best_effort_through_the_updater() -> None:
    text = (ROOT / "install.bat").read_text()
    assert "git pull" not in text and "FETCH_HEAD:src/cap/updater.py" in text
    assert 'set "GIT_TERMINAL_PROMPT=0"' in text and 'set "GCM_INTERACTIVE=never"' in text
    for code in (updater.OK, updater.NOT_UPDATED, updater.RUNNING):
        assert f'"%RC%"=="{code}"' in text
    # Git installed but not on PATH is found before winget is tried; uv kept current
    assert text.index(r"%ProgramFiles%\Git\cmd\git.exe") < text.index("winget install")
    assert "uv self update" in text


def test_only_install_bat_updates_the_folder() -> None:
    """A helper in the folder that pulled rewrote itself while cmd was reading it."""
    for p in _bats():
        if p.name != "install.bat":
            text = p.read_text().lower()
            assert "git pull" not in text and "git fetch" not in text, p.name
