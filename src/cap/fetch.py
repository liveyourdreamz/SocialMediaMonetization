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
    if isinstance(e, BlockedURLError):
        return f"refused: {e}"
    if isinstance(e, httpx.HTTPStatusError):
        return f"HTTP {e.response.status_code} from the site"
    if isinstance(e, httpx.ConnectTimeout | httpx.ReadTimeout):
        return "timed out"
    if isinstance(e, httpx.ConnectError):
        return f"could not connect ({str(e)[:80] or 'network'})"
    return f"{type(e).__name__}: {str(e)[:100]}"


MAX_BYTES = 2_000_000  # stop reading a page after this; 20k characters are kept anyway
MAX_HOPS = 5


class BlockedURLError(RuntimeError):
    pass


def check_public(url: str) -> None:
    """Refuse anything that is not a public http(s) address: loopback, private,
    link-local (cloud metadata), reserved, or a name that resolves to one of those."""
    import ipaddress
    import socket

    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise BlockedURLError("only public http(s) pages")
    host = u.hostname
    try:
        infos = socket.getaddrinfo(host, u.port or (443 if u.scheme == "https" else 80))
    except socket.gaierror as e:
        raise httpx.ConnectError(f"cannot resolve {host}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise BlockedURLError(f"{host} is not a public address")


def _get(c: httpx.Client, url: str, headers: dict[str, str]) -> httpx.Response:
    """GET with redirects followed by hand, every hop checked, the body capped."""
    for _ in range(MAX_HOPS + 1):
        check_public(url)
        with c.stream("GET", url, headers=headers) as r:
            if r.is_redirect and r.headers.get("location"):
                url = str(r.url.join(r.headers["location"]))
                continue
            r.raise_for_status()
            chunks, size = [], 0
            for chunk in r.iter_bytes():
                chunks.append(chunk)
                size += len(chunk)
                if size >= MAX_BYTES:
                    break
            body = b"".join(chunks)
            # iter_bytes() has already undone gzip/brotli/deflate; a copy that kept the
            # Content-Encoding header would decode it a second time (22 Oct: every
            # compressed page failed with "incorrect header check")
            headers = {
                k: v
                for k, v in r.headers.items()
                if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")
            }
            return httpx.Response(r.status_code, headers=headers, content=body, request=r.request)
    raise BlockedURLError("too many redirects")


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
            r = _get(c, target, headers)
            ctype = r.headers.get("content-type", "")
            if "html" in ctype or "xml" in ctype:
                return extract_text(r.text)
            return r.text[:MAX_CHARS], None
        except BlockedURLError:
            raise  # never retried with another User-Agent
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
    with httpx.Client(follow_redirects=False, timeout=timeout) as c:
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
