"""The idea book in Postgres (cap.ideabook has the rules). A decision updates only a
row that is still shown, so two taps on Pick never decide one idea twice."""

from __future__ import annotations

import datetime as dt
from typing import Any

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from cap.ideabook import MEMORY_DAYS, IdeaBook, IdeaDecidedError, IdeaNotFoundError, _decide
from cap.schema.ideas import Idea, IdeaStatus


def _idea(row: tuple[Any, ...]) -> Idea:
    return Idea.model_validate(row[0])


class PostgresIdeaBook(IdeaBook):
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def add(self, ideas: list[Idea]) -> None:
        with self._pool.connection() as conn, conn.transaction():
            for i in ideas:
                conn.execute(
                    "INSERT INTO ideas (id, channel, week, status, created_at, record)"
                    " VALUES (%s, %s, %s, %s, %s, %s)",
                    (i.idea_id, i.channel, i.week, i.status.value, i.created_at,
                     Jsonb(i.model_dump(mode="json"))),
                )  # fmt: skip

    def get(self, idea_id: str) -> Idea:
        with self._pool.connection() as conn:
            row = conn.execute("SELECT record FROM ideas WHERE id = %s", (idea_id,)).fetchone()
        if row is None:
            raise IdeaNotFoundError(idea_id)
        return _idea(row)

    def week(self, channel: str, monday: dt.date, shown_only: bool = True) -> list[Idea]:
        sql = "SELECT record FROM ideas WHERE channel = %s AND week = %s"
        if shown_only:
            sql += " AND status NOT IN ('held', 'canary')"
        with self._pool.connection() as conn:
            rows = conn.execute(sql, (channel, monday)).fetchall()
        return sorted((_idea(r) for r in rows), key=lambda i: (i.rank == 0, i.rank))

    def decide(
        self, idea_id: str, status: IdeaStatus, topic_edit: str = "", post_id: str | None = None
    ) -> Idea:
        done = _decide(self.get(idea_id), status, topic_edit, post_id)
        with self._pool.connection() as conn:
            cur = conn.execute(
                "UPDATE ideas SET status = %s, record = %s WHERE id = %s AND status = 'shown'",
                (done.status.value, Jsonb(done.model_dump(mode="json")), idea_id),
            )
        if cur.rowcount != 1:  # decided by someone else in between
            raise IdeaDecidedError(f"idea {idea_id} was already decided")
        return done

    def take_for_canary(self, idea_id: str) -> Idea:
        taken = self.get(idea_id).model_copy(update={"status": IdeaStatus.CANARY})
        with self._pool.connection() as conn:
            cur = conn.execute(
                "UPDATE ideas SET status = 'canary', record = %s WHERE id = %s AND status = 'shown'",
                (Jsonb(taken.model_dump(mode="json")), idea_id),
            )
        if cur.rowcount != 1:
            raise IdeaDecidedError(f"idea {idea_id} is no longer shown")
        return taken

    def expire(self, before: dt.date) -> int:
        with self._pool.connection() as conn:
            cur = conn.execute(
                "UPDATE ideas SET status = 'expired',"
                " record = jsonb_set(record, '{status}', '\"expired\"')"
                " WHERE status = 'shown' AND week < %s",
                (before,),
            )
        return cur.rowcount

    def recent(self, channel: str, days: int = MEMORY_DAYS) -> list[Idea]:
        since = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT record FROM ideas WHERE channel = %s AND created_at >= %s"
                " ORDER BY created_at DESC",
                (channel, since),
            ).fetchall()
        return [_idea(r) for r in rows]
