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


def per_article(url, daily):  # /per-article/ daily series, one item per day with data
    start = url.split("/daily/")[1].split("/")[0][:8]
    first = dt.datetime.strptime(start, "%Y%m%d").date()
    return {"items": [
        {"granularity": "daily", "timestamp": f"{first + dt.timedelta(days=n):%Y%m%d}00", "views": v}
        for n, v in enumerate(daily) if v is not None]}  # fmt: skip


def fake_api(days, history, fail=()):
    def fetch(url):
        if any(f in url for f in fail):
            raise RuntimeError("503")
        if "/top/" in url:
            return top(days)
        title = url.split("/user/")[1].split("/")[0]
        if title not in history:
            raise RuntimeError("404")
        return per_article(url, history[title])

    return fetch


def hist(baseline, window):  # 30 baseline days then 7 window days; None = no data
    return [*baseline, *window]


DAYS = [
    ("Main_Page", 900_000),  # not a topic
    ("Special:Search", 80_000),  # not a topic
    ("Perovskite_solar_cell", 6_000),  # 6,000 a day against 2,000 a day: ratio 3
    ("Moon", 4_000),  # flat
    ("Brand_new_page", 1_000),  # history only this week: a new page, capped ratio
    ("Tiny_topic", 100),  # under MIN_VIEWS
]
HISTORY = {
    "Perovskite_solar_cell": hist([2_000] * 30, [6_000] * 7),
    "Moon": hist([4_000] * 30, [4_000] * 7),
    "Brand_new_page": hist([None] * 30, [1_000] * 7),
    "Tiny_topic": hist([10] * 30, [100] * 7),
}


def test_rising_topics_are_signals_strongest_first():
    out = d.signals("A", TODAY, fake_api(DAYS, HISTORY))
    assert [(s.title, s.views_7d, s.ratio_to_baseline) for s in out] == [
        ("Brand new page", 7_000, d.NEW_PAGE_RATIO),
        ("Perovskite solar cell", 42_000, 3.0),
    ]
    assert [s.signal_id for s in out] == ["d-01", "d-02"]


# ---- review R10 -------------------------------------------------------------------------


def test_a_failed_lookup_drops_the_page_and_most_failing_is_unavailable():
    one_fails = fake_api(DAYS, HISTORY, fail=["Brand_new_page"])
    assert [s.title for s in d.signals("A", TODAY, one_fails)] == ["Perovskite solar cell"]
    with pytest.raises(d.DemandUnavailableError, match="article lookups failed"):
        d.signals("A", TODAY, fake_api(DAYS, HISTORY, fail=["/per-article/"]))


def test_a_page_a_few_days_old_is_capped_not_a_thousandfold_riser():
    young = {**HISTORY, "Brand_new_page": hist([None] * 27 + [5, 5, 5], [1_000] * 7)}
    out = {s.title: s.ratio_to_baseline for s in d.signals("A", TODAY, fake_api(DAYS, young))}
    assert out["Brand new page"] == d.NEW_PAGE_RATIO


def test_a_missing_window_day_does_not_shrink_the_ratio():
    gappy = {**HISTORY, "Perovskite_solar_cell": hist([2_000] * 30, [6_000, None, *[6_000] * 5])}
    out = {s.title: s for s in d.signals("A", TODAY, fake_api(DAYS, gappy))}
    assert out["Perovskite solar cell"].ratio_to_baseline == 3.0
    assert out["Perovskite solar cell"].views_7d == 42_000


def test_the_week_counts_every_day_not_only_top_list_days():
    """The top lists only pick candidates; views_7d is the article's own week."""
    days = [("Perovskite_solar_cell", 6_000)]
    hist_ = {"Perovskite_solar_cell": hist([2_000] * 30, [9_000] * 7)}
    (s,) = d.signals("A", TODAY, fake_api(days, hist_))
    assert s.views_7d == 63_000 and s.ratio_to_baseline == 4.5


@pytest.mark.parametrize(
    "title",
    [
        "User_talk:Jimbo",
        "Wikipedia_talk:Sandbox",
        "Draft:Foo",
        "Template_talk:Infobox",
        "Module:Citation",
        "نقاش_المستخدم:س",
        "مسودة:شيء",
    ],  # fmt: skip
)
def test_namespace_pages_are_not_topics(title):
    assert not d.is_topic(title)


def test_a_colon_in_an_article_title_is_not_a_namespace():
    assert d.is_topic("Star_Wars:_Episode_IV_–_A_New_Hope") and d.is_topic("علوم")


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
