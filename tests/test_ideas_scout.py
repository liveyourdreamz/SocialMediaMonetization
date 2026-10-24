"""Story feeds and the two P4 tools (D48): code runs them, IDs bound the fetches."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from cap.ideas import feeds
from cap.ideas.feeds import FeedItem, week_items
from cap.ideas.scout import ScoutTools

TODAY = dt.date(2026, 10, 26)
RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Solar module prices fall again</title><link>https://www.positive.news/environment/solar-prices/</link>
<description>Prices fell by a tenth this year.</description><pubDate>Thu, 22 Oct 2026 09:00:00 +0000</pubDate></item>
<item><title>Old story</title><link>https://www.positive.news/old/</link><pubDate>Mon, 01 Sep 2026 09:00:00 +0000</pubDate></item>
<item><title>Off-site link</title><link>https://elsewhere.example/solar</link><pubDate>Thu, 22 Oct 2026 09:00:00 +0000</pubDate></item>
</channel></rss>"""
ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Why did renewables become so cheap?</title>
<link rel="alternate" href="https://ourworldindata.org/cheap-renewables-growth"/>
<summary>Solar power fell 89% in price in a decade.</summary><published>2026-10-21T00:00:00Z</published></entry>
</feed>"""
BOMB = b"""<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;">]>
<rss><channel><item><title>&lol2;</title></item></channel></rss>"""


def fetch_feeds(url):
    if "positive.news" in url:
        return RSS
    if "ourworldindata" in url:
        return ATOM
    raise ConnectionError("feed down")


def test_this_weeks_items_from_rss_and_atom_on_their_own_prefix_only():
    items, failed = week_items("A", TODAY, fetch_feeds)
    assert sorted((i.title, i.source) for i in items) == [
        ("Solar module prices fall again", "Positive News"),
        ("Why did renewables become so cheap?", "Our World in Data"),
    ]  # the old item and the off-site link are dropped
    assert len(failed) == len(feeds.FEEDS) - 2 and all("ConnectionError" in f for f in failed)


def test_a_feed_with_entities_is_refused_not_expanded():
    with pytest.raises(Exception, match="(?i)entit"):
        feeds.parse(BOMB)


def scout(fetched=None):
    items = [FeedItem("Solar module prices fall again", "https://www.positive.news/environment/solar-prices/",
                      "Prices fell by a tenth.", TODAY, "Positive News")]  # fmt: skip

    def wiki(query, lang):
        return [("Solar cell", f"https://{lang}.wikipedia.org/wiki/Solar_cell"),
                ("Bad", "https://www.reddit.com/r/solar")]  # fmt: skip

    def page(url):
        (fetched if fetched is not None else []).append(url)
        return "page text " * 10

    return ScoutTools("A", items, wiki, page)


def test_search_issues_ids_and_fetch_opens_only_those():
    opened: list[str] = []
    s = scout(opened)
    res = json.loads(s.search({"query": "solar prices"}))["results"]
    assert [(r["id"], r["role"]) for r in res] == [("r-001", "story"), ("r-002", "verification")]
    assert all("reddit" not in json.dumps(r) for r in res)  # blocked even from Wikipedia's search
    body = json.loads(s.fetch({"id": "r-002"}))
    assert body["url"] == "https://en.wikipedia.org/wiki/Solar_cell" and opened == [body["url"]]
    with pytest.raises(PermissionError):
        s.fetch({"id": "https://evil.example/"})  # a raw URL is not an ID
    with pytest.raises(PermissionError):
        s.fetch({"id": "r-999"})
    assert s.depth2_attempts == 2 and s.fetches == 1


def test_the_same_page_keeps_its_id_and_evidence_resolves_to_issued_urls_only():
    s = scout()
    s.search({"query": "solar"})
    s.search({"query": "solar prices again"})
    assert len(s.issued) == 2
    assert s.resolve(["r-001", "r-404"]) == ["https://www.positive.news/environment/solar-prices/"]


def test_the_tools_are_declared_with_strict_inputs():
    names = [t.name for t in scout().tools()]
    assert names == ["search_allowlisted", "fetch_allowlisted"]
    assert all(t.input_schema["additionalProperties"] is False for t in scout().tools())
