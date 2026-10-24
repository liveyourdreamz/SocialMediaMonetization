"""Schema migrations for the Postgres store: numbered SQL files in cap/store/sql.

Each file runs once, in its own transaction, in name order; `schema_migrations` records
which have run. An advisory lock makes two app processes starting together take turns
instead of both applying the same file. A file is never edited once released: a change
is a new file.
"""

from __future__ import annotations

from pathlib import Path

from psycopg import Connection

SQL_DIR = Path(__file__).with_name("sql")
_LOCK_KEY = 0x434150  # "CAP": one advisory lock for migrations, held per transaction


def pending(conn: Connection) -> list[Path]:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
    )
    done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()}
    return [f for f in sorted(SQL_DIR.glob("*.sql")) if f.stem not in done]


def apply(conn: Connection) -> list[str]:
    """Apply every pending migration; return the versions applied."""
    applied: list[str] = []
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_KEY,))
        for f in pending(conn):
            with conn.transaction():
                conn.execute(f.read_text(encoding="utf-8"))  # type: ignore[arg-type]
                conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (f.stem,))
            applied.append(f.stem)
    return applied
