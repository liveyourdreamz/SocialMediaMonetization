"""Story feeds: the week's items from the constructive sources (D48), read by code.

Each story source with a published feed (RSS 2.0 or Atom) is read once per run. An
item is kept only when its link falls under that source's allowed prefix, so a feed
can never smuggle in a link to somewhere else. Feeds come from outside, so they are
parsed with defusedxml (no entity expansion, no external entities). The feed URLs are
provisional until the first real run reads them: a feed that fails is recorded and
skipped, and the run goes on.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from email.utils import parsedate_to_datetime

from defusedxml import ElementTree

from cap.sources import story_source

# story-source prefix -> its feed; sources without a known feed are searched only
FEEDS: dict[str, str] = {
    "ourworldindata.org/": "https://ourworldindata.org/atom.xml",
    "earthobservatory.nasa.gov/": "https://earthobservatory.nasa.gov/feeds/earth-observatory.rss",
    "www.positive.news/": "https://www.positive.news/feed/",
    "reasonstobecheerful.world/": "https://reasonstobecheerful.world/feed/",
    "news.un.org/ar/": "https://news.un.org/feed/subscribe/ar/news/all/rss.xml",
}
ATOM = "{http://www.w3.org/2005/Atom}"
MAX_SUMMARY = 600

FetchBytes = Callable[[str], bytes]


@dataclass(frozen=True)
class FeedItem:
    title: str  # untrusted text from outside
    url: str
    summary: str  # untrusted
    published: dt.date | None
    source: str  # the story source's name


def _date(text: str | None) -> dt.date | None:
    if not text:
        return None
    try:
        return parsedate_to_datetime(text).date()  # RSS: RFC 822
    except (TypeError, ValueError):
        pass
    try:
        return dt.datetime.fromisoformat(text.strip().replace("Z", "+00:00")).date()  # Atom
    except ValueError:
        return None


def _text(el: object | None) -> str:
    return " ".join(("".join(el.itertext()) if el is not None else "").split())  # type: ignore[attr-defined]


def parse(data: bytes) -> list[tuple[str, str, str, dt.date | None]]:
    """(title, link, summary, date) for every item of an RSS 2.0 or Atom feed."""
    root = ElementTree.fromstring(data)
    out = []
    for it in root.iter("item"):
        out.append((_text(it.find("title")), _text(it.find("link")),
                    _text(it.find("description"))[:MAX_SUMMARY], _date(_text(it.find("pubDate")))))  # fmt: skip
    for en in root.iter(f"{ATOM}entry"):
        link = next((ln.get("href", "") for ln in en.findall(f"{ATOM}link")
                     if ln.get("rel", "alternate") == "alternate"), "")  # fmt: skip
        summary = en.find(f"{ATOM}summary")
        if summary is None:
            summary = en.find(f"{ATOM}content")
        when = en.find(f"{ATOM}published")
        if when is None:
            when = en.find(f"{ATOM}updated")
        out.append(
            (_text(en.find(f"{ATOM}title")), link, _text(summary)[:MAX_SUMMARY], _date(_text(when)))
        )
    return out


def week_items(
    channel: str, today: dt.date, fetch: FetchBytes, days: int = 7
) -> tuple[list[FeedItem], list[str]]:
    """This channel's story items from the last `days` days, and the feeds that failed."""
    since = today - dt.timedelta(days=days)
    items: list[FeedItem] = []
    failed: list[str] = []
    for prefix, feed in FEEDS.items():
        try:
            entries = parse(fetch(feed))
        except Exception as e:  # noqa: BLE001 - one feed down never stops the run
            failed.append(f"{feed}: {type(e).__name__}")
            continue
        for title, url, summary, published in entries:
            src = story_source(url, channel)
            if src is None or src.prefix != prefix:  # off-prefix links are dropped
                continue
            if published is not None and published < since:
                continue
            items.append(FeedItem(title, url, summary, published, src.name))
    return items, failed
