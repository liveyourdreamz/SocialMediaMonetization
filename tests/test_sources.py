"""The source library (PRD 5, 4.7, D48): which URL may play which role."""

from __future__ import annotations

import pytest

from cap.sources import STORY_SOURCES, allowed, is_blocked, story_source, verification_ok


@pytest.mark.parametrize(
    ("url", "channel", "role"),
    [
        ("https://ourworldindata.org/cheap-renewables-growth", "A", "story"),
        ("https://earthobservatory.nasa.gov/images/123", "B", "story"),  # listed: story wins
        ("https://news.un.org/ar/story/2026/10/1", "C", "story"),
        (
            "https://news.un.org/ar/story/2026/10/1",
            "A",
            "verification",
        ),  # a C story; A verifies anywhere
        ("https://news.un.org/ar/story/2026/10/1", "B", None),  # not a C story, not on B's list
        ("https://en.wikipedia.org/wiki/Monsoon", "B", "verification"),
        ("https://www.example-vendor.com/docs/sync", "A", "verification"),  # A: any unblocked
        ("https://www.example-vendor.com/docs/sync", "B", None),  # B: the science list only
        ("https://www.reddit.com/r/science/x", "A", None),
        ("https://ourworldindata.org/comments/42", "A", None),  # UGC path under a story prefix
        ("http://ourworldindata.org/energy", "A", "verification"),  # stories need https
    ],
)
def test_each_url_gets_its_role(url, channel, role):
    assert allowed(url, channel) == role


def test_every_story_source_names_its_licence_and_status():
    for s in STORY_SOURCES:
        assert s.licence and s.status in ("confirmed", "provisional")
        assert not s.prefix.startswith("http") and "/" in s.prefix
    confirmed = {s.name for s in STORY_SOURCES if s.status == "confirmed"}
    assert confirmed == {"Our World in Data", "NASA Earth Observatory"}


def test_blocked_and_ugc_paths_are_never_sources():
    assert is_blocked("https://x.com/someone/status/1")
    assert is_blocked("https://www.positive.news/forum/thread")
    assert story_source("https://www.positive.news/forum/thread", "A") is None
    assert not verification_ok("https://medium.com/@a/post", "A")
