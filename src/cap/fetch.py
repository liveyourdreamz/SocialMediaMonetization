"""Source fetcher: URLs in a brief -> SourceDoc dicts for the drafter and the fact
gate. Scrappy stand-in for the trend-research agent (P4): a human supplies the
URLs, this pulls the text. Fetched text is untrusted and goes nowhere except the
model client's untrusted-text boundary (it is never rendered raw into a prompt)."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup

MAX_CHARS = 20_000
UA = "ContentAgentPortfolio/0.1 (+research fetch; contact via channel description)"


def extract_text(html: str) -> tuple[str, str | None]:
    """Readable text and a page date if one is declared."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript", "svg"]):
        tag.decompose()
    date = None
    for sel, attr in (
        ("meta[property='article:published_time']", "content"),
        ("meta[name='date']", "content"),
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


def fetch_sources(urls: list[str], timeout: float = 20.0) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    now = datetime.now(UTC).isoformat()
    with httpx.Client(follow_redirects=True, timeout=timeout, headers={"User-Agent": UA}) as c:
        for url in urls:
            url = url.strip()
            if not url.startswith(("http://", "https://")):
                continue
            try:
                r = c.get(url)
                r.raise_for_status()
                ctype = r.headers.get("content-type", "")
                body, date = extract_text(r.text) if "html" in ctype else (r.text[:MAX_CHARS], None)
            except Exception as e:  # a dead link is a missing source, not a crash
                body, date = f"[fetch failed: {type(e).__name__}]", None
            out.append({"url": url, "body": body, "fetched_at": now, "page_date": date})
    return out
