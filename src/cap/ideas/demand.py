"""Demand: what people are curious about this week, measured from Wikipedia pageviews
(PRD 4.7, D48). Code measures it; no model ever estimates popularity (D24).

How a signal is made, for one Wikipedia project (English for channels A and B,
Arabic for C):

1. The project's daily top-1,000 lists for the last 7 days are summed per article:
   that is views_7d, and it picks the candidates.
2. Pages that are not topics are dropped: the main page, special and talk pages,
   searches, and anything with a namespace prefix.
3. For the most-viewed candidates, the article's own daily views over the 30 days
   before the window give its baseline; ratio_to_baseline is the 7-day daily average
   divided by the baseline daily average. A page new this month has no baseline and
   its ratio is capped, so a brand-new spike neither wins outright nor vanishes.
4. Signals rising at least MIN_RATIO times, with at least MIN_VIEWS views, are kept,
   strongest ratio first.

The Wikimedia REST API is official, free and asks for a User-Agent naming the
caller (cap.fetch.UA carries the repository's address). Every call goes through one
injectable function, so tests run on recorded responses.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

API = "https://wikimedia.org/api/rest_v1/metrics/pageviews"
PROJECTS = {"A": "en.wikipedia", "B": "en.wikipedia", "C": "ar.wikipedia"}
WINDOW_DAYS, BASELINE_DAYS = 7, 30
MAX_CANDIDATES = 120  # per-article baseline calls made per run
MIN_VIEWS, MIN_RATIO = 5_000, 1.5
NEW_PAGE_RATIO = 5.0  # cap for a page with no baseline yet
SKIP_PREFIXES = ("Special:", "Wikipedia:", "Portal:", "File:", "Help:", "Category:",
                 "Template:", "Talk:", "User:", "خاص:", "ويكيبيديا:", "بوابة:", "ملف:",
                 "مساعدة:", "تصنيف:", "قالب:", "نقاش:", "مستخدم:")  # fmt: skip
SKIP_TITLES = {"Main_Page", "-", "Search", "الصفحة_الرئيسية"}

Fetch = Callable[[str], Any]  # url -> parsed JSON; raises on failure


class DemandUnavailableError(RuntimeError):
    """Wikimedia could not be read; the run goes on from stories and seeds (4.7)."""


@dataclass(frozen=True)
class DemandSignal:
    signal_id: str
    title: str  # untrusted: an article title from outside, tagged when sent to a model
    project: str
    views_7d: int
    ratio_to_baseline: float


def is_topic(title: str) -> bool:
    return title not in SKIP_TITLES and not title.startswith(SKIP_PREFIXES)


def _day(d: dt.date) -> str:
    return d.strftime("%Y/%m/%d")


def top_url(project: str, day: dt.date) -> str:
    return f"{API}/top/{project}/all-access/{_day(day)}"


def article_url(project: str, title: str, start: dt.date, end: dt.date) -> str:
    article = quote(title, safe="")
    return (
        f"{API}/per-article/{project}/all-access/user/{article}/daily/"
        f"{start:%Y%m%d}00/{end:%Y%m%d}00"
    )


def week_views(project: str, today: dt.date, fetch: Fetch) -> dict[str, int]:
    """Views per article over the 7 full days before `today`, from the daily top
    lists. A day that fails to load is skipped; all days failing is unavailable."""
    totals: dict[str, int] = {}
    loaded = 0
    for back in range(1, WINDOW_DAYS + 1):
        try:
            data = fetch(top_url(project, today - dt.timedelta(days=back)))
        except Exception:  # noqa: BLE001 - one missing day lowers the counts, no more
            continue
        loaded += 1
        for item in data["items"][0]["articles"]:
            title = item["article"]
            if is_topic(title):
                totals[title] = totals.get(title, 0) + int(item["views"])
    if not loaded:
        raise DemandUnavailableError(f"no {project} top lists could be read")
    return totals


def baseline_daily(project: str, title: str, today: dt.date, fetch: Fetch) -> float | None:
    """The article's average daily views over the 30 days before the window, or None
    when it has no history (new page) or the call fails."""
    end = today - dt.timedelta(days=WINDOW_DAYS + 1)
    start = end - dt.timedelta(days=BASELINE_DAYS - 1)
    try:
        items = fetch(article_url(project, title, start, end))["items"]
    except Exception:  # noqa: BLE001 - treated as no baseline
        return None
    views = [int(i["views"]) for i in items]
    return sum(views) / BASELINE_DAYS if views and sum(views) else None


def ratio(views_7d: int, baseline: float | None) -> float:
    if baseline is None:
        return NEW_PAGE_RATIO
    return round(min((views_7d / WINDOW_DAYS) / baseline, 1000.0), 2)


def signals(channel: str, today: dt.date, fetch: Fetch) -> list[DemandSignal]:
    """The week's rising articles for a channel, strongest ratio first."""
    project = PROJECTS[channel]
    week = week_views(project, today, fetch)
    candidates = sorted(week.items(), key=lambda kv: kv[1], reverse=True)[:MAX_CANDIDATES]
    found: list[tuple[str, int, float]] = []
    for title, views in candidates:
        if views < MIN_VIEWS:
            break
        r = ratio(views, baseline_daily(project, title, today, fetch))
        if r >= MIN_RATIO:
            found.append((title, views, r))
    found.sort(key=lambda t: (t[2], t[1]), reverse=True)
    return [
        DemandSignal(f"d-{n:02d}", title.replace("_", " "), project, views, r)
        for n, (title, views, r) in enumerate(found, start=1)
    ]


def http_fetch(timeout: float = 20.0) -> Fetch:
    """The real fetch: Wikimedia's API with the platform's User-Agent."""
    import httpx

    from cap.fetch import UA

    client = httpx.Client(timeout=timeout, headers={"User-Agent": UA}, follow_redirects=False)

    def get(url: str) -> Any:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.json()

    return get
