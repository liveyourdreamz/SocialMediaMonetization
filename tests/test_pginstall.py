"""The portable PostgreSQL download is checked before anything is unpacked (D45)."""

from __future__ import annotations

import hashlib
import shutil
import zipfile
from pathlib import Path

import pytest

from cap import pginstall


def _zip(path: Path, names: list[str]) -> str:
    with zipfile.ZipFile(path, "w") as z:
        for n in names:
            z.writestr(n, f"contents of {n}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def fake_download(tmp_path, monkeypatch):
    src = tmp_path / "served.zip"

    def serve(names: list[str], pin: str | None = None) -> None:
        digest = _zip(src, names)
        monkeypatch.setattr(pginstall, "SHA256", pin or digest)

        def download(url: str, to: Path) -> str:
            shutil.copy(src, to)
            return hashlib.sha256(to.read_bytes()).hexdigest()

        monkeypatch.setattr(pginstall, "_download", download)

    return serve


def test_only_the_server_parts_are_unpacked_and_it_installs_once(fake_download, tmp_path):
    fake_download(
        [
            "pgsql/bin/pg_ctl.exe",
            "pgsql/lib/plpgsql.dll",
            "pgsql/share/postgres.bki",
            "pgsql/pgAdmin 4/big.exe",
            "pgsql/StackBuilder/sb.exe",
        ]
    )
    dest = tmp_path / "postgres"
    assert pginstall.install(dest, out=lambda _: None)
    assert (dest / "pgsql" / "bin" / "pg_ctl.exe").exists()
    assert not (dest / "pgsql" / "pgAdmin 4").exists()
    assert pginstall.installed(dest)
    assert not pginstall.install(dest, out=lambda _: None)  # already there: no download


def test_a_download_that_does_not_match_is_refused_and_nothing_is_unpacked(fake_download, tmp_path):
    fake_download(["pgsql/bin/pg_ctl.exe"], pin="0" * 64)
    dest = tmp_path / "postgres"
    with pytest.raises(pginstall.InstallError, match="checksum"):
        pginstall.install(dest, out=lambda _: None)
    assert not dest.exists()


def test_a_path_that_escapes_the_folder_is_refused(fake_download, tmp_path):
    fake_download(["pgsql/bin/../../../escaped.exe"])
    with pytest.raises(pginstall.InstallError, match="unsafe path"):
        pginstall.install(tmp_path / "postgres", out=lambda _: None)
    assert not (tmp_path / "escaped.exe").exists()
