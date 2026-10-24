"""Demand from Wikipedia pageviews (PRD 4.7, D48): code measures it, on recorded
responses in the API's own shape."""

from __future__ import annotations

import datetime as dt

import pytest

from cap.ideas import demand as d

TODAY = dt.date(2026, 10, 26)


def top(articles):  # one day of /top/, as the API returns it
    return {"items": [{"project": "en.wikipedia", "access": "all-access", "articles": [
        {"article": a, "views": v, "rank": i + 1} for i, (a, v) in enumerate(articles)]}]}  # fmt: skip


def per_article(daily):  # /per-article/ daily series
    return {"items": [{"granularity": "daily", "views": v} for v in daily]}


def fake_api(days, history, fail=()):
    def fetch(url):
        if any(f in url for f in fail):
            raise RuntimeError("503")
        if "/top/" in url:
            return top(days)
        title = url.split("/user/")[1].split("/")[0]
        if title not in history:
            raise RuntimeError("404")
        return per_article(history[title])

    return fetch


DAYS = [
    ("Main_Page", 900_000),  # not a topic
    ("Special:Search", 80_000),  # not a topic
    ("Perovskite_solar_cell", 6_000),  # 42,000 a week against 2,000 a day: ratio 3
    ("Moon", 4_000),  # 28,000 a week against 4,000 a day: flat
    ("Brand_new_page", 1_000),  # 7,000 a week, no history: capped ratio
    ("Tiny_topic", 100),  # under MIN_VIEWS
]
HISTORY = {
    "Perovskite_solar_cell": [2_000] * 30,
    "Moon": [4_000] * 30,
    "Tiny_topic": [10] * 30,
}


def test_rising_topics_are_signals_strongest_first():
    out = d.signals("A", TODAY, fake_api(DAYS, HISTORY))
    assert [(s.title, s.views_7d, s.ratio_to_baseline) for s in out] == [
        ("Brand new page", 7_000, d.NEW_PAGE_RATIO),
        ("Perovskite solar cell", 42_000, 3.0),
    ]
    assert [s.signal_id for s in out] == ["d-01", "d-02"]


def test_pages_that_are_not_topics_never_count():
    week = d.week_views("en.wikipedia", TODAY, fake_api(DAYS, HISTORY))
    assert "Main_Page" not in week and "Special:Search" not in week
    assert not d.is_topic("تصنيف:علوم") and d.is_topic("علوم")


def test_a_missing_day_lowers_the_counts_and_no_days_is_unavailable():
    day3 = (TODAY - dt.timedelta(days=3)).strftime("%Y/%m/%d")
    week = d.week_views("en.wikipedia", TODAY, fake_api(DAYS, HISTORY, fail=[day3]))
    assert week["Moon"] == 4_000 * 6
    with pytest.raises(d.DemandUnavailableError):
        d.week_views("en.wikipedia", TODAY, fake_api(DAYS, HISTORY, fail=["/top/"]))


def test_the_urls_are_the_documented_endpoints():
    assert d.top_url("ar.wikipedia", dt.date(2026, 10, 19)) == (
        "https://wikimedia.org/api/rest_v1/metrics/pageviews/top/ar.wikipedia/all-access/2026/10/19"
    )
    url = d.article_url("en.wikipedia", "AC/DC", dt.date(2026, 9, 12), dt.date(2026, 10, 11))
    assert url.endswith(
        "/per-article/en.wikipedia/all-access/user/AC%2FDC/daily/2026091200/2026101100"
    )


def test_channel_c_reads_arabic_wikipedia():
    assert d.PROJECTS == {"A": "en.wikipedia", "B": "en.wikipedia", "C": "ar.wikipedia"}
