"""The two tools trend research (P4) may use, run by code (Prompt Pack P4, D45, D48).

* search_allowlisted(query): matches the week's story-feed items and asks Wikipedia's
  own search (verification), returning results with IDs issued in this run.
* fetch_allowlisted(id): opens the page behind an ID issued in this run, through the
  fetcher's address checks. A raw URL or an unissued ID is refused and counted as an
  attempted depth-2 fetch, so depth 1 is a capability, not a request.

Everything a tool returns came from outside; the loop wraps it as untrusted. The IDs
also let code resolve P4's evidence_ids to the URLs it actually issued, so a model can
never cite a page nobody fetched.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from cap.ideas.feeds import FeedItem
from cap.models.loop import Tool
from cap.sources import allowed

MAX_STORY_RESULTS, MAX_WIKI_RESULTS = 5, 3
MAX_PAGE_CHARS = 12_000
WikiSearch = Callable[[str, str], list[tuple[str, str]]]  # (query, lang) -> [(title, url)]
FetchPage = Callable[[str], str]  # url -> readable text; raises on failure

_WORD = re.compile(r"\w+", re.UNICODE)


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if len(w) > 2}


@dataclass
class Issued:
    url: str
    role: str  # story or verification
    title: str


@dataclass
class ScoutTools:
    channel: str
    feed_items: list[FeedItem]
    wiki_search: WikiSearch
    fetch_page: FetchPage
    issued: dict[str, Issued] = field(default_factory=dict)
    depth2_attempts: int = 0
    fetches: int = 0

    def _issue(self, url: str, role: str, title: str) -> str:
        for rid, it in self.issued.items():
            if it.url == url:
                return rid
        rid = f"r-{len(self.issued) + 1:03d}"
        self.issued[rid] = Issued(url, role, title)
        return rid

    def search(self, args: dict[str, object]) -> str:
        query = str(args.get("query", ""))[:200]
        want = _words(query)
        scored = sorted(
            ((len(want & _words(f"{i.title} {i.summary}")), i) for i in self.feed_items),
            key=lambda t: t[0],
            reverse=True,
        )
        results = [
            {"id": self._issue(i.url, "story", i.title), "role": "story", "title": i.title,
             "source": i.source, "published": i.published.isoformat() if i.published else None,
             "summary": i.summary}
            for hits, i in scored[:MAX_STORY_RESULTS] if hits
        ]  # fmt: skip
        lang = "ar" if self.channel == "C" else "en"
        for title, url in self.wiki_search(query, lang)[:MAX_WIKI_RESULTS]:
            if allowed(url, self.channel):
                results.append({"id": self._issue(url, "verification", title),
                                "role": "verification", "title": title})  # fmt: skip
        return json.dumps({"query": query, "results": results}, ensure_ascii=False)

    def fetch(self, args: dict[str, object]) -> str:
        rid = str(args.get("id", ""))
        it = self.issued.get(rid)
        if it is None:
            self.depth2_attempts += 1
            raise PermissionError(f"{rid!r} was not issued by a search in this run")
        if not allowed(it.url, self.channel):  # the list may have changed mid-run
            raise PermissionError("that page is no longer on the allow-list")
        self.fetches += 1
        text = self.fetch_page(it.url)[:MAX_PAGE_CHARS]
        return json.dumps({"id": rid, "url": it.url, "role": it.role, "text": text},
                          ensure_ascii=False)  # fmt: skip

    def resolve(self, evidence_ids: list[str]) -> list[str]:
        """The URLs behind evidence IDs this run issued; anything else is dropped."""
        return [self.issued[r].url for r in evidence_ids if r in self.issued]

    def tools(self) -> list[Tool]:
        return [
            Tool(
                "search_allowlisted",
                "Search this week's story sources and Wikipedia. Returns results with IDs "
                "issued in this run; only those IDs can be fetched.",
                {"type": "object", "properties": {"query": {"type": "string"}},
                 "required": ["query"], "additionalProperties": False},
                self.search,
            ),
            Tool(
                "fetch_allowlisted",
                "Fetch the page behind a result ID from search_allowlisted in this run.",
                {"type": "object", "properties": {"id": {"type": "string"}},
                 "required": ["id"], "additionalProperties": False},
                self.fetch,
            ),
        ]  # fmt: skip
