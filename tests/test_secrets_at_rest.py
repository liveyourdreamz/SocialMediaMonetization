"""22 Oct review: secrets never reach a template or an error, and the files that hold
them are the owner's alone (0600 on POSIX, the user's own ACL on Windows)."""

from __future__ import annotations

import os
import stat
import subprocess

import pytest

from cap import connections as conn

PAGE_TOKEN = "EAAGpagetoken0123456789secret"


@pytest.fixture
def meta_connected(tmp_path, monkeypatch):
    from cap.config import get_settings

    monkeypatch.setenv("CAP_DATA_DIR", str(tmp_path / "d"))
    get_settings.cache_clear()
    conn.set_credentials("meta", {"app_id": "1234567890", "app_secret": "s" * 32})
    data = conn.load("meta")
    data["token"] = {"access_token": "EAAGusertoken987654321"}
    data["account"] = {"name": "Falak Page", "id": "42", "page_token": PAGE_TOKEN}
    conn.save("meta", data)
    yield tmp_path
    get_settings.cache_clear()


def test_the_meta_page_token_never_reaches_a_template(meta_connected):
    s = conn.status("meta")
    assert s["account"] == {"name": "Falak Page", "id": "42"}
    assert PAGE_TOKEN not in repr(s)
    assert conn.load("meta")["account"]["page_token"] == PAGE_TOKEN  # the publisher's copy


def test_the_connections_page_never_shows_the_page_token(meta_connected, monkeypatch):
    from cap.store import MemoryStore
    from tests.test_ui import Browser, create_app

    monkeypatch.chdir(meta_connected)
    api = Browser(create_app(MemoryStore()), follow_redirects=False)
    api.sign_in("hemant")
    html = api.get("/connections").text
    assert "Falak Page" in html and PAGE_TOKEN not in html


def test_safe_error_redacts_the_page_token(meta_connected):
    text = conn.safe_error(RuntimeError(f"POST /video_reels?access_token={PAGE_TOKEN}"), "meta")
    assert PAGE_TOKEN not in text and "[redacted]" in text


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes")
def test_posix_secret_files_are_0600(meta_connected, tmp_path):
    from cap.setup import ensure_local_secrets, write_key

    f = meta_connected / "d" / "connections" / "meta.json"
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert stat.S_IMODE(f.parent.stat().st_mode) == 0o700
    env = tmp_path / ".env"
    env.write_text("")
    os.chmod(env, 0o644)
    ensure_local_secrets(env)
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    os.chmod(env, 0o644)
    write_key(env, "sk-ant-test")
    assert stat.S_IMODE(env.stat().st_mode) == 0o600


def test_windows_restricts_the_acl_with_icacls_and_no_shell(tmp_path, monkeypatch):
    """os.chmod is a no-op on Windows: the .env and connection writes call icacls."""
    from cap import secretfile
    from cap.config import get_settings
    from cap.setup import write_key

    calls: list[tuple[list[str], dict]] = []

    def run(argv, **kw):
        calls.append((argv, kw))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setenv("CAP_DATA_DIR", str(tmp_path / "d"))
    get_settings.cache_clear()
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(secretfile, "WINDOWS", True)
    monkeypatch.setenv("USERNAME", "hemant")
    monkeypatch.setenv("USERDOMAIN", "LAPTOP")
    env = tmp_path / ".env"
    write_key(env, "sk-ant-test")
    conn.set_credentials("meta", {"app_id": "1234567890", "app_secret": "s" * 32})
    monkeypatch.undo()
    get_settings.cache_clear()
    argvs = [argv for argv, _ in calls]
    assert ["icacls", str(env), "/inheritance:r", "/grant:r", "LAPTOP\\hemant:F"] in argvs
    folder = tmp_path / "d" / "connections"
    assert [
        "icacls",
        str(folder),
        "/inheritance:r",
        "/grant:r",
        "LAPTOP\\hemant:(OI)(CI)F",
    ] in argvs
    assert any(a[1].startswith(str(folder)) and a[-1] == "LAPTOP\\hemant:F" for a in argvs)
    assert all(not kw.get("shell") for _, kw in calls)


def test_windows_acl_failure_warns_and_the_write_stands(tmp_path, monkeypatch):
    from cap import secretfile

    def run(argv, **kw):
        return subprocess.CompletedProcess(argv, 5, "", "Access is denied.")

    monkeypatch.setattr(secretfile, "WINDOWS", True)
    monkeypatch.setattr(secretfile.subprocess, "run", run)
    monkeypatch.setenv("USERNAME", "hemant")
    f = tmp_path / ".env"
    f.write_text("CAP_X=1\n")
    with pytest.warns(UserWarning, match="Access is denied"):
        secretfile.restrict_to_owner(f)
    assert f.read_text() == "CAP_X=1\n"
