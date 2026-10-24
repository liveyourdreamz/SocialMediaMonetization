"""The source library (PRD section 5; 4.7 and D48): every place an agent may read.

Three roles, kept apart:

* verification: where a claim is checked. Agencies, references and journals for the
  science channels (domains, subdomains included); channel A may use any site that
  is not blocked.
* story: where trend research looks for something constructive happening (D48). These
  are URL path prefixes, not bare domains, each with its licence and whether a real
  fetch has confirmed it. Headlines and links only; their text is never republished.
* blocked: social media and user-generated content, never a source for any role.

A path that accepts third-party content (comments, forums, user pages) is refused even
under an allowed prefix (Prompt Pack section 2). Changing this list is a reviewed
change, like a prompt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

# Verification sources for channels B and C. Subdomains are included.
SCIENCE_DOMAINS = [
    "wikipedia.org",
    "britannica.com",
    "nasa.gov",
    "noaa.gov",
    "usgs.gov",
    "esa.int",
    "copernicus.eu",
    "nist.gov",
    "weather.gov",
    "metoffice.gov.uk",
    "who.int",
    "iea.org",
    "nature.com",
    "science.org",
    "pnas.org",
    "plos.org",
    "royalsociety.org",
    "sciencedirect.com",
    "springer.com",
    "doi.org",
    "mit.edu",
    "stanford.edu",
    "ox.ac.uk",
    "cam.ac.uk",
    "scientificamerican.com",
    "newscientist.com",
]
# Not sources for any channel (PRD 4.7: social media and UGC are never sources).
BLOCKED_DOMAINS = [
    "facebook.com",
    "instagram.com",
    "tiktok.com",
    "x.com",
    "twitter.com",
    "reddit.com",
    "youtube.com",
    "quora.com",
    "pinterest.com",
    "threads.net",
    "linkedin.com",
    "medium.com",
    "substack.com",
]

# Path segments that accept third-party content; refused under any prefix.
# ("wiki" is not here: Wikipedia's articles live under /wiki/; GitHub wikis are not a
# source, since github.com is on no list.)
UGC_SEGMENTS = {"comments", "comment", "forum", "forums", "community", "discussion",
                "discussions", "issues", "users", "gist", "pull", "profile"}  # fmt: skip

Status = Literal["confirmed", "provisional"]


@dataclass(frozen=True)
class StorySource:
    prefix: str  # host plus path, no scheme: "ourworldindata.org/"
    name: str
    licence: str
    status: Status  # confirmed: terms read and a page opened; provisional: until a real fetch
    channels: tuple[str, ...] = ("A", "B", "C")


STORY_SOURCES: tuple[StorySource, ...] = (
    StorySource("ourworldindata.org/", "Our World in Data",
                "CC BY for its own charts and data; third-party data under the provider's terms",
                "confirmed"),
    StorySource("earthobservatory.nasa.gov/", "NASA Earth Observatory",
                "reuse allowed with credit to NASA, except items marked copyright", "confirmed"),
    StorySource("www.positive.news/", "Positive News", "headlines and links only", "provisional",
                ("A", "B")),
    StorySource("reasonstobecheerful.world/", "Reasons to be Cheerful",
                "headlines and links only", "provisional", ("A", "B")),
    StorySource("unstats.un.org/sdgs/", "UN SDG data", "UN terms of use", "provisional"),
    StorySource("www.unep.org/ar/", "UNEP news and stories (Arabic)", "UN terms of use",
                "provisional", ("C",)),
    StorySource("news.un.org/ar/", "UN News (Arabic)", "UN terms of use", "provisional", ("C",)),
)  # fmt: skip


def _host_path(url: str) -> tuple[str, str]:
    p = urlparse(url)
    return p.netloc.lower(), p.path or "/"


def _under(host: str, domains: list[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def has_ugc_path(url: str) -> bool:
    _, path = _host_path(url)
    return any(seg.lower() in UGC_SEGMENTS for seg in path.split("/") if seg)


def is_blocked(url: str) -> bool:
    host, _ = _host_path(url)
    return _under(host, BLOCKED_DOMAINS) or has_ugc_path(url)


def story_source(url: str, channel: str) -> StorySource | None:
    """The story source a URL falls under for this channel, or None."""
    if urlparse(url).scheme != "https" or is_blocked(url):
        return None
    host, path = _host_path(url)
    here = host + path
    return next(
        (s for s in STORY_SOURCES if channel in s.channels and here.startswith(s.prefix)), None
    )


def verification_ok(url: str, channel: str) -> bool:
    """May a claim for this channel be checked against this URL?"""
    if urlparse(url).scheme not in ("https", "http") or is_blocked(url):
        return False
    host, _ = _host_path(url)
    return channel == "A" or _under(host, SCIENCE_DOMAINS)


def allowed(url: str, channel: str) -> Literal["story", "verification"] | None:
    """The role a URL may play for a channel's trend research, or None (not on the list)."""
    if story_source(url, channel) is not None:
        return "story"
    return "verification" if verification_ok(url, channel) else None
