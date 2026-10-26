"""The laptop's own Postgres: set up once, started, reached by password, stopped
(D45, ADR-006). Runs wherever the Postgres programs are found and the user is not
root (initdb refuses root); CI sets CAP_REQUIRE_PG=1 so it cannot be skipped there."""

from __future__ import annotations

import os
import socket

import psycopg
import pytest

from cap import localpg


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def bin_dir():
    found = localpg.find_bin(os.environ.get("CAP_PG_BIN", ""))
    if found is None or (hasattr(os, "geteuid") and os.geteuid() == 0):
        if os.environ.get("CAP_REQUIRE_PG") == "1":
            pytest.fail(
                "CAP_REQUIRE_PG=1 but the Postgres programs are missing (or running as root)"
            )
        pytest.skip("no Postgres programs here, or running as root")
    return found


def test_set_up_start_use_and_stop(bin_dir, tmp_path):
    data, port = tmp_path / "cap-data", _free_port()
    with localpg.database_url("", data, str(bin_dir), port) as url:
        assert (data / "pg" / "PG_VERSION").exists()
        assert "127.0.0.1" in url and str(port) in url
        with psycopg.connect(url) as conn:
            assert conn.execute("SELECT current_database()").fetchone() == ("cap",)
        # a wrong password is refused: the database is not open to anyone on the laptop
        bad = psycopg.conninfo.make_conninfo(url, password="wrong")
        with pytest.raises(psycopg.OperationalError):
            psycopg.connect(bad).close()
        conf = (data / "pg" / "postgresql.conf").read_text(encoding="utf-8")
        assert "listen_addresses = '127.0.0.1'" in conf
    pg = localpg.LocalPostgres(bin_dir, data, port)
    assert not pg.running()  # stopped when the app stopped


def test_a_second_start_keeps_the_data_and_password(bin_dir, tmp_path):
    data, port = tmp_path / "cap-data", _free_port()
    with localpg.database_url("", data, str(bin_dir), port) as url:
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute("CREATE TABLE kept (x int)")
    password = (data / "pg-password").read_text(encoding="utf-8")
    with localpg.database_url("", data, str(bin_dir), port) as url:
        with psycopg.connect(url) as conn:
            assert conn.execute("SELECT count(*) FROM kept").fetchone() == (0,)
    assert (data / "pg-password").read_text(encoding="utf-8") == password


def test_a_configured_url_is_used_as_given(tmp_path):
    with localpg.database_url("postgresql://x@db/cap", tmp_path, "", 1) as url:
        assert url == "postgresql://x@db/cap"
    assert not (tmp_path / "pg").exists()


def test_missing_programs_say_what_to_do(tmp_path, monkeypatch):
    monkeypatch.setattr(localpg, "find_bin", lambda configured="": None)
    with pytest.raises(localpg.LocalPostgresError, match="install.bat"):
        with localpg.database_url("", tmp_path, "", 54329):
            pass
