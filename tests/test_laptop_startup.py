"""Start-up on the laptop never stops on one bad input (review H8 and the notes after).

* one stored record an update made unreadable is quarantined, the rest load (H8);
* old records migrate step by step to the current schema_version;
* .env is read as UTF-8 on every platform (Arabic, smart quotes, Notepad's BOM);
* the API key is asked for once, and only a usable key is ever saved;
* the phone path prints real URLs, the sign-in secrets and the firewall command;
* a second launch opens the first instead of touching its records.
"""

from __future__ import annotations

import json
import logging
import socket
from pathlib import Path

import pytest
from pydantic import ValidationError

from cap import cli, instance, lan
from cap.approval.app import FileStore, create_app
from cap.config import ENV_FILE_ENCODING, Settings, get_settings
from cap.demo.seed import new_brief
from cap.schema.migrate import MIGRATIONS, load_record
from cap.schema.post_record import SCHEMA_VERSION, PostRecord, PostStatus
from cap.setup import (
    DECLINED,
    KeyFormatError,
    clean_key,
    declined,
    ensure_env,
    read_key,
    write_key,
)

# ---- stored records (H8) ---------------------------------------------------------------


def _saved(store: FileStore, **changes: object) -> Path:
    rec = new_brief("A", "a topic")
    store.put(rec)
    path = store.root / "posts" / f"{rec.post_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(changes)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_unknown_fields_from_another_build_do_not_stop_start_up(tmp_path: Path) -> None:
    path = _saved(FileStore(tmp_path), old_field=1)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["brief"]["retired_setting"] = "x"
    path.write_text(json.dumps(data), encoding="utf-8")
    store = FileStore(tmp_path)
    assert len(store.posts) == 1 and not store.quarantined


def test_one_unreadable_record_is_quarantined_and_the_rest_load(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    first = FileStore(tmp_path)
    good = _saved(first)
    bad = _saved(first, status="no-such-status")
    junk = tmp_path / "posts" / "junk.json"
    junk.write_bytes(b"{not json")
    with caplog.at_level(logging.WARNING, logger="cap.store"):
        store = FileStore(tmp_path)
    assert list(store.posts) == [good.stem]
    q = tmp_path / "posts" / "quarantine"
    for f in (bad, junk):
        assert not f.exists() and (q / f.name).exists()
        assert "could not read it" in (q / f"{f.name}.error.txt").read_text(encoding="utf-8")
    assert len([r for r in caplog.records if r.name == "cap.store"]) == 2  # one line each
    assert len(FileStore(tmp_path).posts) == 1  # and the next start is quiet


def test_a_broken_state_file_is_quarantined(tmp_path: Path) -> None:
    (tmp_path / "state.json").write_text("[1, 2", encoding="utf-8")
    store = FileStore(tmp_path)
    assert store.paused is False and (tmp_path / "quarantine" / "state.json").exists()


def test_a_v1_record_migrates_and_interrupted_work_is_parked(tmp_path: Path) -> None:
    path = _saved(FileStore(tmp_path), status=PostStatus.DRAFTING.value)
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["schema_version"]  # written before the field existed
    path.write_text(json.dumps(data), encoding="utf-8")
    rec = next(iter(FileStore(tmp_path).posts.values()))
    assert rec.schema_version == SCHEMA_VERSION and rec.status == PostStatus.PARKED
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == SCHEMA_VERSION


def test_every_schema_version_step_has_a_migration() -> None:
    assert sorted(MIGRATIONS) == list(range(1, SCHEMA_VERSION))
    loaded = load_record(json.dumps({"channel_id": "A", "schema_version": SCHEMA_VERSION + 1}))
    assert loaded.record.schema_version == SCHEMA_VERSION  # a newer build's file still loads


def test_the_strict_base_still_refuses_unknown_fields() -> None:
    """Model output is validated with the same base: it must stay strict."""
    from cap.agents.outputs import RuleFindingOut

    with pytest.raises(ValidationError):
        PostRecord.model_validate({"channel_id": "A", "invented": 1})
    with pytest.raises(ValidationError):
        RuleFindingOut.model_validate({"invented": 1})


# ---- .env encoding ---------------------------------------------------------------------


def test_env_is_read_as_utf8_with_arabic_and_smart_quotes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pydantic_settings.sources.providers import dotenv as provider

    used: list[str | None] = []
    real = provider.dotenv_values

    def spy(path: Path, encoding: str | None = None) -> dict[str, str | None]:
        used.append(encoding)
        return real(path, encoding=encoding)

    monkeypatch.setattr(provider, "dotenv_values", spy)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CAP_ARABIC_REVIEWERS", raising=False)
    (tmp_path / ".env").write_text(
        '\ufeffCAP_ARABIC_REVIEWERS=["ليلى"]\nCAP_PILOT_START=2026-09-08 “go live”\n',
        encoding="utf-8",
    )
    s = Settings()
    assert s.arabic_reviewers == ["ليلى"] and "“go live”" in s.pilot_start
    assert used == [ENV_FILE_ENCODING] == ["utf-8"]  # never the locale's cp1252


def test_a_cp1252_env_is_rewritten_as_utf8(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_bytes("CAP_PILOT_START=2026 “go”\n".encode("cp1252"))
    said: list[str] = []
    ensure_env(env, ask=lambda _: "", out=said.append)
    assert "“go”" in env.read_text(encoding="utf-8")
    assert any("UTF-8" in s for s in said)


# ---- the API key -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "pasted",
    [
        "sk-ant-abc",
        '  "sk-ant-abc"  ',
        "'sk-ant-abc'",
        "“sk-ant-abc”",
        "CAP_ANTHROPIC_API_KEY=sk-ant-abc",
        'export CAP_ANTHROPIC_API_KEY="sk-ant-abc"\n',
    ],
)
def test_pasted_keys_are_cleaned(pasted: str) -> None:
    assert clean_key(pasted) == "sk-ant-abc"


@pytest.mark.parametrize("pasted", ["sk-ant-ébc", "sk-ant abc", "sk-ant abc"])
def test_unusable_keys_are_refused(pasted: str, tmp_path: Path) -> None:
    with pytest.raises(KeyFormatError):
        clean_key(pasted)
    with pytest.raises(KeyFormatError):
        write_key(tmp_path / ".env", pasted)


def test_a_refused_key_is_asked_again_and_never_saved(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    answers = iter(["sk-ant-é", "CAP_ANTHROPIC_API_KEY='sk-ant-ok'"])
    said: list[str] = []
    assert ensure_env(env, ask=lambda _: next(answers), out=said.append) == "anthropic"
    assert read_key(env) == "sk-ant-ok"
    text = env.read_text(encoding="utf-8")
    assert "é" not in text and "CAP_ANTHROPIC_API_KEY=sk-ant-ok\n" in text
    assert any("Try again" in s for s in said)


def test_mock_mode_is_asked_once_not_on_every_command(tmp_path: Path) -> None:
    """install.bat runs `cap check` then `cap serve`: both used to ask."""
    env = tmp_path / ".env"
    asked: list[str] = []

    def ask(prompt: str) -> str:
        asked.append(prompt)
        return ""

    assert ensure_env(env, ask=ask, out=lambda _: None) == "mock"  # cap check
    assert ensure_env(env, ask=ask, out=lambda _: None) == "mock"  # cap serve
    assert len(asked) == 1 and declined(env) and DECLINED in env.read_text(encoding="utf-8")
    ensure_env(env, force=True, ask=lambda _: "sk-ant-later", out=lambda _: None)  # cap key
    assert read_key(env) == "sk-ant-later" and not declined(env)


def test_a_saved_key_with_quotes_or_prefix_is_tidied(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text('CAP_ANTHROPIC_API_KEY="CAP_ANTHROPIC_API_KEY=sk-ant-x"\n', encoding="utf-8")
    assert ensure_env(env, ask=lambda _: "unused", out=lambda _: None) == "anthropic"
    assert "CAP_ANTHROPIC_API_KEY=sk-ant-x\n" in env.read_text(encoding="utf-8")


def test_a_saved_non_ascii_key_is_cleared_and_asked_for(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("CAP_MODEL_BACKEND=anthropic\nCAP_ANTHROPIC_API_KEY=sk-’x\n", "utf-8")
    said: list[str] = []
    assert ensure_env(env, ask=lambda _: "sk-ant-new", out=said.append) == "anthropic"
    assert read_key(env) == "sk-ant-new" and any("cannot have" in s for s in said)


# ---- the phone path --------------------------------------------------------------------


def test_lan_addresses_are_private_ipv4_only() -> None:
    got = lan.lan_ipv4(["127.0.0.1", "192.168.1.20", "169.254.3.4", "8.8.8.8", "10.0.0.5"])
    assert got == ["192.168.1.20", "10.0.0.5"]
    assert all(lan._is_lan(a) for a in lan.lan_ipv4())  # whatever this machine has


def test_phone_block_has_urls_secrets_and_the_firewall_command() -> None:
    block = lan.phone_block(8000, {"hemant": "123456"}, ["192.168.1.20"])
    assert "http://192.168.1.20:8000/login" in block and "hemant: 123456" in block
    assert (
        'netsh advfirewall firewall add rule name="Content Agent Portfolio 8000" dir=in '
        "action=allow protocol=TCP localport=8000 profile=private" in block
    )
    assert "<this laptop" not in block


def test_the_start_up_banner_shows_each_persons_own_code(tmp_path: Path) -> None:
    """The banner shows the per-person codes cap.setup wrote into .env (one each for
    hemant and layla); the old shared passcode is gone."""
    from cap.setup import ensure_local_secrets

    env = tmp_path / ".env"
    env.write_text("CAP_MODEL_BACKEND=mock\nCAP_LOCAL_PASSCODE=654321\n", encoding="utf-8")
    ensure_local_secrets(env)
    codes = cli.signin_secrets(env)
    assert set(codes) == {"hemant", "layla"} and len(set(codes.values())) == 2
    assert "654321" not in codes.values() and "CAP_LOCAL_PASSCODE" not in env.read_text()


def test_operator_settings_show_the_phone_urls(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from cap.ui import templates

    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(templates.env.globals, "phone_urls", ["http://192.168.1.20:8000/login"])
    from tests.test_ui import Browser

    api = Browser(create_app(FileStore(tmp_path / "data")), follow_redirects=False)
    api.sign_in("hemant")
    page = api.get("/operator").text
    assert "On your phone" in page and "http://192.168.1.20:8000/login" in page


# ---- double launch ---------------------------------------------------------------------


def test_the_instance_lock_is_exclusive_and_says_the_port(tmp_path: Path) -> None:
    with instance.InstanceLock(tmp_path, 8123):
        with pytest.raises(instance.AlreadyRunningError) as e:
            instance.InstanceLock(tmp_path, 8000).acquire()
        assert e.value.port == 8123
    instance.InstanceLock(tmp_path, 8000).acquire()  # released with the first


def _serve_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    monkeypatch.chdir(tmp_path)
    data = tmp_path / "cap-data"
    monkeypatch.setenv("CAP_DATA_DIR", str(data))
    get_settings.cache_clear()
    store = FileStore(data)
    rec = new_brief("A", "in flight in the first window")
    rec.status = PostStatus.DRAFTING
    store.put(rec)
    return data, data / "posts" / f"{rec.post_id}.json"


def test_a_second_launch_leaves_the_first_windows_posts_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:  # type: ignore[no-untyped-def]
    data, post = _serve_env(tmp_path, monkeypatch)
    before = post.read_bytes()
    with instance.InstanceLock(data, 8000):
        assert cli.main(["serve", "--port", "8000"]) == cli.ALREADY_RUNNING
    assert post.read_bytes() == before  # not marked interrupted
    assert "already running" in capsys.readouterr().out


def test_a_busy_port_is_found_before_the_records_are_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:  # type: ignore[no-untyped-def]
    _data, post = _serve_env(tmp_path, monkeypatch)
    before = post.read_bytes()
    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen()
        port = srv.getsockname()[1]
        assert cli.main(["serve", "--port", str(port)]) == cli.ALREADY_RUNNING
    assert post.read_bytes() == before
    assert f"port {port}" in capsys.readouterr().out
