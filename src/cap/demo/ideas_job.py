"""The weekly trend-research run as a queued job, and the clock that queues it (D48).

At or after Monday 08:00 Dubai time (settings; ADR-017) the worker queues one IDEAS job
per channel whose Digest has no ideas yet this week. The job's key is
"ideas-<channel>-<monday>", so the queue never holds the same week twice, and a week
that already has ideas is not run again. The Operator screen can queue a run now.
Each run's report goes to the audit log, where the Operator screen reads it.
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from urllib.parse import unquote, urlparse

import httpx

from cap.config import get_settings
from cap.ideas import demand
from cap.ideas.feeds import week_items
from cap.ideas.week import Sources, monday_of, run_week
from cap.jobs import Job, JobKind
from cap.store import PostStore

MAX_FEED_BYTES = 3_000_000


def job_key(channel: str, monday: dt.date) -> str:
    return f"ideas-{channel}-{monday.isoformat()}"


def parse_key(key: str) -> tuple[str, dt.date]:
    _, channel, day = key.split("-", 2)
    return channel, dt.date.fromisoformat(day)


def local_now(now: dt.datetime | None = None) -> dt.datetime:
    s = get_settings()
    now = now or dt.datetime.now(dt.UTC)
    return now.astimezone(dt.timezone(dt.timedelta(hours=s.ideas_utc_offset_hours)))


def due(now: dt.datetime | None = None) -> dt.date | None:
    """This week's Monday when the run time has passed this week, else None."""
    s, local = get_settings(), local_now(now)
    monday = monday_of(local.date())
    start = dt.datetime.combine(monday + dt.timedelta(days=s.ideas_weekday),
                                dt.time(s.ideas_hour), local.tzinfo)  # fmt: skip
    return monday if local >= start else None


def schedule_due(store: PostStore, now: dt.datetime | None = None) -> list[str]:
    """Queue this week's run for every channel that has no ideas yet; the keys queued."""
    monday = due(now)
    if monday is None:
        return []
    queued = []
    for ch in get_settings().channels:
        key = job_key(ch, monday)
        if store.ideas.week(ch, monday, shown_only=False) or store.jobs.jobs_for(key):
            continue
        store.jobs.enqueue(JobKind.IDEAS, key)
        queued.append(key)
    return queued


def queue_now(store: PostStore, channel: str) -> str:
    key = job_key(channel, monday_of(local_now().date()))
    store.jobs.enqueue(JobKind.IDEAS, key, {"manual": True})
    return key


# ---- the outside world, for real ------------------------------------------------------


def _fetch_bytes(url: str) -> bytes:
    from cap.fetch import UA
    from cap.safehttp import PinnedTransport

    # the same address checks as every fetch: public addresses only, pinned (SSRF)
    with httpx.Client(transport=PinnedTransport(), timeout=20.0, headers={"User-Agent": UA},
                      follow_redirects=False, trust_env=False) as c:  # fmt: skip
        r = c.get(url)
        r.raise_for_status()
        return r.content[:MAX_FEED_BYTES]


def _wiki_search(query: str, lang: str) -> list[tuple[str, str]]:
    from cap.research import _search_wikipedia

    urls = _search_wikipedia(query, limit=3, lang=lang)
    return [(unquote(urlparse(u).path.rsplit("/", 1)[-1]).replace("_", " "), u) for u in urls]


def _fetch_page(url: str) -> str:
    from cap.fetch import failed_reason, fetch_sources

    doc = fetch_sources([url])[0]
    why = failed_reason(doc)
    if why:
        raise RuntimeError(why)
    return str(doc["body"])


def real_sources() -> Sources:
    wikimedia = demand.http_fetch()
    return Sources(
        demand=lambda ch, today: demand.signals(ch, today, wikimedia),
        feeds=lambda ch, today: week_items(ch, today, _fetch_bytes),
        wiki_search=_wiki_search,
        fetch_page=_fetch_page,
    )


def run_ideas_job(
    store: PostStore, job: Job, sources: Sources | None = None, client: Any = None
) -> None:
    """The worker's handler for an IDEAS job."""
    from cap.demo import happy_fixtures
    from cap.models import get_client

    channel, monday = parse_key(job.post_id)
    report = run_week(channel, monday, client or get_client(happy_fixtures()), store.ideas,
                      sources or real_sources())  # fmt: skip
    store.audit.append("ideas run", detail={"channel": channel, "week": monday.isoformat(),
                                            "report": report.line()})  # fmt: skip
