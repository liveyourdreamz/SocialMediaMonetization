"""Demand: what people are curious about this week, measured from Wikipedia pageviews
(PRD 4.7, D48). Code measures it; no model ever estimates popularity (D24).

How a signal is made, for one Wikipedia project (English for channels A and B,
Arabic for C):

1. The project's daily top-1,000 lists for the last 7 days are summed per article;
   that only picks the candidates.
2. Pages that are not topics are dropped: the main page, searches, and every
   namespace page (special, talk of any kind, drafts, templates, modules, ...).
3. For each candidate, one call fetches the article's own daily views over the 30
   days before the window and the 7 days of it. views_7d is the window's daily
   average times 7 (not just the days it made a top list), and ratio_to_baseline is
   the window's daily average over the baseline's, each averaged over the days that
   loaded. A page with under 21 days of baseline is new: its ratio is capped, so a
   brand-new spike neither wins outright nor vanishes.
4. A candidate whose lookup fails is dropped, never guessed (a rate limit once turned
   every page into a riser, review R10); when most lookups fail, demand is
   unavailable and the run says so.
5. Signals rising at least MIN_RATIO times, with at least MIN_VIEWS views, are kept,
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
NEW_PAGE_RATIO = 5.0  # the ratio of a page with too little history to compare
MIN_HISTORY_DAYS = 21  # baseline days needed to compare against
NAMESPACES = {
    "Special", "Wikipedia", "Portal", "File", "Help", "Category", "Template", "Talk",
    "User", "Draft", "Module", "MediaWiki", "TimedText", "Book", "Gadget",
    "Gadget_definition", "Topic",
    "خاص", "ويكيبيديا", "بوابة", "ملف", "مساعدة", "تصنيف", "قالب", "مستخدم", "مسودة",
    "وحدة", "ميدياويكي",
}  # fmt: skip
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
    """An article, not a page about the wiki. A colon alone is not a namespace (many
    article titles have one); only a known namespace, or any talk namespace, is."""
    if title in SKIP_TITLES:
        return False
    ns, colon, _ = title.partition(":")
    if not colon:
        return True
    return not (ns in NAMESPACES or ns.endswith("_talk") or ns.startswith("نقاش"))


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


@dataclass(frozen=True)
class Series:
    """An article's daily views: the 30 days before the window, and the window."""

    baseline: list[int]
    window: list[int]

    def week(self) -> int:
        """Views over the window, from its daily average (a day that did not load
        does not shrink it)."""
        return round(sum(self.window) / len(self.window) * WINDOW_DAYS) if self.window else 0

    def ratio(self) -> float | None:
        """This week's daily average over the baseline's; NEW_PAGE_RATIO for a page with
        too little history; None with no window data."""
        if not self.window:
            return None
        week = sum(self.window) / len(self.window)
        if len(self.baseline) < MIN_HISTORY_DAYS or not sum(self.baseline):
            return NEW_PAGE_RATIO
        return round(min(week / (sum(self.baseline) / len(self.baseline)), 1000.0), 2)


def series(project: str, title: str, today: dt.date, fetch: Fetch) -> Series:
    """One call for the baseline and the window, split by each day's timestamp.
    Raises when the call fails: the caller drops the page."""
    first = today - dt.timedelta(days=BASELINE_DAYS + WINDOW_DAYS)
    window_from = today - dt.timedelta(days=WINDOW_DAYS)
    items = fetch(article_url(project, title, first, today - dt.timedelta(days=1)))["items"]
    baseline: list[int] = []
    window: list[int] = []
    for item in items:
        day = dt.datetime.strptime(str(item["timestamp"])[:8], "%Y%m%d").date()
        (window if day >= window_from else baseline).append(int(item["views"]))
    return Series(baseline, window)


def signals(channel: str, today: dt.date, fetch: Fetch) -> list[DemandSignal]:
    """The week's rising articles for a channel, strongest ratio first."""
    project = PROJECTS[channel]
    week = week_views(project, today, fetch)
    candidates = [(t, v) for t, v in sorted(week.items(), key=lambda kv: kv[1], reverse=True)
                  if v >= MIN_VIEWS][:MAX_CANDIDATES]  # fmt: skip
    found: list[tuple[str, int, float]] = []
    failed = 0
    for title, _ in candidates:
        try:
            s = series(project, title, today, fetch)
        except Exception:  # noqa: BLE001 - dropped, never guessed (review R10)
            failed += 1
            continue
        r, views = s.ratio(), s.week()
        if r is not None and r >= MIN_RATIO and views >= MIN_VIEWS:
            found.append((title, views, r))
    if candidates and failed * 2 > len(candidates):
        raise DemandUnavailableError(
            f"{failed} of {len(candidates)} {project} article lookups failed"
        )
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
