"""Put the official portable PostgreSQL programs on a Windows laptop (D45, ADR-006).

`cap db install` (called by tools/start.bat) downloads EnterpriseDB's PostgreSQL zip,
the archive the PostgreSQL project links to for Windows binaries, checks its SHA-256
against the value pinned here, and unpacks only what the server needs (bin, lib,
share) into <app>/postgres/pgsql. No installer, no Windows service, no admin rights.
A matching copy already in place is left alone, so it downloads once.

The pin was taken from the file as GitHub's Windows runner downloaded it; a download
that does not match is deleted and refused, never unpacked. Changing the version means
changing URL, VERSION and SHA256 together, in one reviewed commit.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path

import httpx

from cap.localpg import APP_ROOT

VERSION = "16.15-1"
URL = "https://sbp.enterprisedb.com/getfile.jsp?fileid=1260623"
# taken 23 Oct 2026 from the file as GitHub's Windows runner downloaded it (373,254,386 bytes)
SHA256 = "43bb45f173a6f08cf1d29a97a6d8deb119e8e8093a24c00d2d1001a0ccaa8281"
DEST = APP_ROOT / "postgres"
KEEP = ("pgsql/bin/", "pgsql/lib/", "pgsql/share/")
_MARKER = "VERSION"


class InstallError(RuntimeError):
    """The programs could not be put in place; the message says why."""


def installed(dest: Path = DEST) -> bool:
    marker = dest / _MARKER
    return marker.exists() and marker.read_text(encoding="utf-8").strip() == VERSION


def install(dest: Path = DEST, out: Callable[[str], None] = print) -> bool:
    """Download, check and unpack. False when the right version was already there."""
    if installed(dest):
        return False
    with tempfile.TemporaryDirectory() as tmp:
        zpath = Path(tmp) / "pgsql.zip"
        out(f"  downloading PostgreSQL {VERSION} for Windows (about 370 MB, once)...")
        digest = _download(URL, zpath)
        if digest != SHA256:
            raise InstallError(
                "the PostgreSQL download did not match its checksum and was discarded; "
                "try again later"
            )
        out("  checksum ok; unpacking...")
        _unpack(zpath, dest)
    (dest / _MARKER).write_text(VERSION, encoding="utf-8")
    return True


def _download(url: str, to: Path) -> str:
    h = hashlib.sha256()
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=120) as r:
            r.raise_for_status()
            with to.open("wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
                    h.update(chunk)
    except httpx.HTTPError as e:
        raise InstallError(f"could not download PostgreSQL: {e}") from e
    return h.hexdigest()


def _unpack(zpath: Path, dest: Path) -> None:
    staging = dest.with_name(dest.name + ".new")
    if staging.exists():
        shutil.rmtree(staging)
    with zipfile.ZipFile(zpath) as z:
        for member in z.infolist():
            name = member.filename
            if not name.startswith(KEEP) or member.is_dir():
                continue
            target = (staging / name).resolve()
            if not target.is_relative_to(staging.resolve()):  # no path escapes the folder
                raise InstallError(f"unsafe path in the archive: {name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(member) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
    if dest.exists():
        shutil.rmtree(dest)
    staging.rename(dest)
