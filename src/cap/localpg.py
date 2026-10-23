"""The laptop's own Postgres (D45, ADR-006): set up once, started with the app, stopped
when the app stops.

install.bat puts the official portable PostgreSQL binaries in <app>/postgres/pgsql (no
installer, no Windows service, no admin rights). This module runs them:

* the cluster lives in <data_dir>/pg, so a backup of cap-data backs up the database;
* it listens on 127.0.0.1 only, on settings.pg_port (54329), with password sign-in;
* the password is generated once and kept in <data_dir>/pg-password, readable by the
  laptop's owner only (cap.secretfile); it never appears in a log or on screen.

Where the binaries are found, first match wins: settings.pg_bin; <app>/postgres/pgsql/bin;
PGBIN (set on GitHub's Windows runners); /usr/lib/postgresql/<newest>/bin (Linux);
pg_ctl on PATH.
"""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg.conninfo import make_conninfo

from cap.secretfile import restrict_to_owner

APP_ROOT = Path(__file__).resolve().parents[2]
PORTABLE_BIN = APP_ROOT / "postgres" / "pgsql" / "bin"
USER = "cap"
DATABASE = "cap"
EXE = ".exe" if os.name == "nt" else ""


class LocalPostgresError(RuntimeError):
    """The laptop's Postgres could not be set up or started; the message says what to do."""


def find_bin(configured: str = "") -> Path | None:
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured))
    candidates.append(PORTABLE_BIN)
    if os.environ.get("PGBIN"):
        candidates.append(Path(os.environ["PGBIN"]))
    linux = sorted(Path("/usr/lib/postgresql").glob("*/bin"), key=lambda p: p.parent.name)
    candidates.extend(reversed(linux))
    on_path = shutil.which("pg_ctl")
    if on_path:
        candidates.append(Path(on_path).parent)
    for c in candidates:
        if (c / f"pg_ctl{EXE}").exists() and (c / f"initdb{EXE}").exists():
            return c
    return None


@dataclass
class LocalPostgres:
    bin_dir: Path
    data_dir: Path  # the app's data folder (cap-data); the cluster is data_dir / "pg"
    port: int

    @property
    def cluster(self) -> Path:
        return self.data_dir / "pg"

    @property
    def password_file(self) -> Path:
        return self.data_dir / "pg-password"

    @property
    def log_file(self) -> Path:
        return self.data_dir / "pg.log"

    def _run(self, tool: str, *args: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - fixed argv from our own paths, no shell
            [str(self.bin_dir / f"{tool}{EXE}"), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    # ---- one-time set-up --------------------------------------------------------------

    def initialised(self) -> bool:
        return (self.cluster / "PG_VERSION").exists()

    def init(self) -> None:
        """Create the cluster with a fresh password. Never touches an existing cluster."""
        if self.initialised():
            return
        # initdb on Windows drops admin rights first and then cannot always create the
        # folders above the cluster; an empty folder made here is one it accepts
        self.cluster.mkdir(parents=True, exist_ok=True)
        password = secrets.token_urlsafe(24)
        self.password_file.write_text(password, encoding="utf-8")
        restrict_to_owner(self.password_file)
        pwfile = self.data_dir / ".pg-init-pw"
        pwfile.write_text(password, encoding="utf-8")
        restrict_to_owner(pwfile)
        try:
            done = self._run(
                "initdb",
                "-D",
                str(self.cluster),
                "-U",
                USER,
                f"--pwfile={pwfile}",
                "--auth=scram-sha-256",
                "--encoding=UTF8",
                "--no-locale",
            )
        finally:
            pwfile.unlink(missing_ok=True)
        if done.returncode != 0:
            raise LocalPostgresError(f"could not create the database: {_tail(done)}")
        restrict_to_owner(self.cluster)
        with (self.cluster / "postgresql.conf").open("a", encoding="utf-8") as f:
            f.write(
                "\n# cap: the laptop's database, reachable from this laptop only\n"
                f"listen_addresses = '127.0.0.1'\nport = {self.port}\n"
                "unix_socket_directories = ''\n"  # TCP on 127.0.0.1 only; no socket file
            )

    # ---- start and stop -------------------------------------------------------------------

    def running(self) -> bool:
        return (
            self.initialised()
            and self._run("pg_ctl", "status", "-D", str(self.cluster)).returncode == 0
        )

    def start(self) -> bool:
        """Start the server; False when it was already running (then leave it running)."""
        if self.running():
            return False
        done = self._run(
            "pg_ctl", "start", "-D", str(self.cluster), "-l", str(self.log_file), "-w", "-t", "60"
        )
        if done.returncode != 0:
            raise LocalPostgresError(
                f"the database did not start; see {self.log_file}: {_tail(done)}"
            )
        return True

    def stop(self) -> None:
        if self.running():
            self._run("pg_ctl", "stop", "-D", str(self.cluster), "-m", "fast", "-w", "-t", "60")

    def url(self, dbname: str = DATABASE) -> str:
        password = self.password_file.read_text(encoding="utf-8").strip()
        return make_conninfo(
            host="127.0.0.1", port=self.port, user=USER, password=password, dbname=dbname
        )

    def ensure_database(self) -> None:
        """The app's own database inside the cluster (initdb makes only "postgres")."""
        with psycopg.connect(self.url("postgres"), autocommit=True) as conn:
            if not conn.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (DATABASE,)
            ).fetchone():
                conn.execute(f'CREATE DATABASE "{DATABASE}"')


def _tail(done: subprocess.CompletedProcess[str]) -> str:
    return (done.stderr or done.stdout).strip()[-400:]


@contextmanager
def database_url(database_url: str, data_dir: Path, pg_bin: str, port: int) -> Iterator[str]:
    """The URL the app should use. A configured URL is used as given; otherwise the
    laptop's own Postgres is set up if needed, started, and stopped again on exit if
    this call started it."""
    if database_url:
        yield database_url
        return
    bin_dir = find_bin(pg_bin)
    if bin_dir is None:
        raise LocalPostgresError(
            "the database programs are missing; run install.bat again to put them in place"
        )
    pg = LocalPostgres(bin_dir, data_dir, port)
    pg.init()
    started = pg.start()
    pg.ensure_database()
    try:
        yield pg.url()
    finally:
        if started:
            pg.stop()


def stop_if_running(data_dir: Path, pg_bin: str, port: int) -> None:
    """Before the data folder is wiped (cap serve --reset): stop its database first."""
    bin_dir = find_bin(pg_bin)
    if bin_dir is not None:
        LocalPostgres(bin_dir, data_dir, port).stop()
