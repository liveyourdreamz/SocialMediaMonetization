"""Source fetcher: URLs in a brief -> SourceDoc dicts for the drafter and the fact
gate. Scrappy stand-in for the trend-research agent (P4): a human supplies the
URLs, this pulls the text. Fetched text is untrusted and goes nowhere except the
model client's untrusted-text boundary (it is never rendered raw into a prompt).

A failed fetch says why, in the document body and the post's log, as
"[fetch failed: <what>]": a 403 from a site that blocks scripts, a timeout, a bad
certificate. Wikipedia is read through its REST API, which is what it asks bots to
use and which returns clean article HTML.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import unquote, urlparse

import httpx
from bs4 import BeautifulSoup

MAX_CHARS = 20_000
# Sites that gate on the User-Agent want a name, a version and a way to reach the
# operator; a browser-shaped string is the fallback for sites that only serve browsers.
UA = "ContentAgentPortfolio/0.1 (https://github.com/liveyourdreamz/SocialMediaMonetization)"
UA_BROWSER = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0 Safari/537.36"
)
HEADERS = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8", "Accept-Language": "en"}
FAILED = "[fetch failed: "


def extract_text(html: str) -> tuple[str, str | None]:
    """Readable text and a page date if one is declared."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript", "svg"]):
        tag.decompose()
    date = None
    for sel, attr in (
        ("meta[property='article:published_time']", "content"),
        ("meta[name='date']", "content"),
        ("meta[property='article:modified_time']", "content"),
        ("time[datetime]", "datetime"),
    ):
        el = soup.select_one(sel)
        if el and el.get(attr):
            date = str(el.get(attr))[:10]
            break
    main = soup.find("article") or soup.find("main") or soup.body or soup
    text = main.get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text)
    return text[:MAX_CHARS], date


def wikipedia_rest(url: str) -> str | None:
    """A Wikipedia article URL -> the REST endpoint that returns its HTML."""
    u = urlparse(url)
    m = re.match(r"^([a-z]{2,3})\.(?:m\.)?wikipedia\.org$", u.netloc)
    if not m or not u.path.startswith("/wiki/"):
        return None
    title = unquote(u.path[len("/wiki/") :])
    return f"https://{m.group(1)}.wikipedia.org/api/rest_v1/page/html/{title}"


def _why(e: Exception) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        return f"HTTP {e.response.status_code} from the site"
    if isinstance(e, httpx.ConnectTimeout | httpx.ReadTimeout):
        return "timed out"
    if isinstance(e, httpx.ConnectError):
        return f"could not connect ({str(e)[:80] or 'network'})"
    return f"{type(e).__name__}: {str(e)[:100]}"


def fetch_one(c: httpx.Client, url: str) -> tuple[str, str | None]:
    """Body text and page date; raises on failure after the fallbacks."""
    rest = wikipedia_rest(url)
    attempts: list[tuple[str, dict[str, str]]] = []
    if rest:
        attempts.append((rest, {"User-Agent": UA, **HEADERS}))
    attempts.append((url, {"User-Agent": UA, **HEADERS}))
    attempts.append((url, {"User-Agent": UA_BROWSER, **HEADERS}))
    last: Exception | None = None
    for target, headers in attempts:
        try:
            r = c.get(target, headers=headers)
            r.raise_for_status()
            ctype = r.headers.get("content-type", "")
            if "html" in ctype or "xml" in ctype:
                return extract_text(r.text)
            return r.text[:MAX_CHARS], None
        except httpx.HTTPStatusError as e:
            last = e
            if e.response.status_code not in (401, 403, 406, 429, 503):
                break  # a 404 will not improve with another User-Agent
        except Exception as e:  # noqa: BLE001 - recorded; the next attempt may do better
            last = e
    assert last is not None
    raise last


def fetch_sources(urls: list[str], timeout: float = 20.0) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    now = datetime.now(UTC).isoformat()
    with httpx.Client(follow_redirects=True, timeout=timeout) as c:
        for url in urls:
            url = url.strip()
            if not url.startswith(("http://", "https://")):
                continue
            try:
                body, date = fetch_one(c, url)
                if not body.strip():
                    body = f"{FAILED}the page had no readable text]"
            except Exception as e:  # noqa: BLE001 - a dead link is a missing source, not a crash
                body, date = f"{FAILED}{_why(e)}]", None
            out.append({"url": url, "body": body, "fetched_at": now, "page_date": date})
    return out


def failed_reason(doc: dict[str, Any]) -> str | None:
    body = str(doc.get("body", ""))
    return body[len(FAILED) : -1] if body.startswith(FAILED) else None
