"""One channel's weekly trend research, end to end (PRD 4.7 and 4.8, D48).

1. Demand: Wikipedia pageviews, measured by code. If Wikimedia is down the run goes on
   from the story sources and seeds, and the gap is recorded for the Digest.
2. Stories: this week's items from the story feeds.
3. P4 explores with its two tools (cap.ideas.scout) in a bounded loop.
4. P5 judges every candidate. P4's topics and angles were written from outside pages,
   so they reach P5 inside the nonce-keyed tag (the taint rule).
5. Evidence IDs resolve to the URLs this run issued; a candidate left with none has no
   source.
6. Code ranks (cap.ideas.rank), last week's unpicked ideas expire, and this week's are
   saved to the idea book.

Every outside call is injected, so tests run it whole on recorded data.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from cap.agents.outputs import P4Item, P4Output, P5Item, P5Output
from cap.config import get_settings
from cap.ideabook import IdeaBook
from cap.ideas import demand as dm
from cap.ideas.feeds import FeedItem
from cap.ideas.rank import Candidate, build_week
from cap.ideas.scout import FetchPage, ScoutTools, WikiSearch
from cap.models import ModelClient, SourceDoc
from cap.models.loop import run_loop
from cap.prompts import spec, untrusted
from cap.schema.ideas import Idea, IdeaStatus
from cap.sources import STORY_SOURCES

_log = logging.getLogger("cap.ideas")
CHANNELS = {"A": "Patch, English, \"Take it apart\"", "B": "Falak, English, \"From orbit\"",
            "C": "Falak, Modern Standard Arabic, \"From orbit\""}  # fmt: skip
MAX_ITEMS = 45


@dataclass
class Sources:
    """The outside world, injected: real functions in the app, recordings in tests."""

    demand: Callable[[str, dt.date], list[dm.DemandSignal]]
    feeds: Callable[[str, dt.date], tuple[list[FeedItem], list[str]]]
    wiki_search: WikiSearch
    fetch_page: FetchPage


@dataclass
class WeekReport:
    channel: str
    monday: dt.date
    shown: int = 0
    held: int = 0
    gaps: list[str] = field(default_factory=list)
    depth2_attempts: int = 0
    blocked: str = ""  # why the week produced nothing, when it did not

    def line(self) -> str:
        head = f"channel {self.channel}, week of {self.monday:%d %b}: {self.shown} ideas, {self.held} held"
        if self.blocked:
            head += f"; stopped: {self.blocked}"
        if self.gaps:
            head += "; gaps: " + "; ".join(self.gaps)
        return head


def monday_of(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=day.weekday())


def _p4_message(
    channel: str, signals: list[dm.DemandSignal], memory: list[Idea], seeds: list[str]
) -> Callable[[str], str]:
    def build(nonce: str) -> str:
        sig = (
            "\n".join(
                f"{s.signal_id} views_7d={s.views_7d} ratio_to_baseline={s.ratio_to_baseline} "
                f"title={untrusted(s.title, nonce)}"
                for s in signals
            )
            or "none this week (Wikimedia unavailable or nothing rising)"
        )
        recent = "\n".join(untrusted(i.topic_edit or i.topic, nonce) for i in memory[:60]) or "none"
        allow = "\n".join(f"story: {s.prefix} ({s.name}; {s.licence})" for s in STORY_SOURCES
                          if channel in s.channels)  # fmt: skip
        s = get_settings()
        return (
            f"<channel>{channel}: {CHANNELS[channel]}</channel>\n<persona>{s.persona}</persona>\n"
            f"<demand_signals>\n{sig}\n</demand_signals>\n<allowlist>\n{allow}\n"
            "verification: the source library for this channel</allowlist>\n"
            f"<lookback_days>7</lookback_days>\n<seeds>{'; '.join(seeds) or 'none'}</seeds>\n"
            f"<recent_topics>\n{recent}\n</recent_topics>\n<max_items>{MAX_ITEMS}</max_items>"
        )

    return build


def _score(client: ModelClient, channel: str, items: list[P4Item]) -> list[P5Item] | None:
    """P5 on every candidate, matched to the items by the number each judgement echoes
    (review: a reordered reply must never attach a verdict to the wrong topic); None
    unless every item got exactly one judgement."""
    body = json.dumps(
        [{"n": n, "topic": i.topic, "angle": i.angle, "timing": i.timing,
          "evidence_urls": i.evidence_urls, "source_gap": i.source_gap} for n, i in enumerate(items)],
        ensure_ascii=False,
    )  # fmt: skip
    s = get_settings()
    user = (
        f"<channel_profile>{channel}: {CHANNELS[channel]}</channel_profile>\n"
        f"<persona>{s.persona}</persona>\n<sensitive_areas>health, money, law, politics, children"
        f"</sensitive_areas>\n<items>judge every item, in order, one judgement each</items>"
    )
    res = client.run(spec("P5"), user, P5Output, sources=[SourceDoc("p4://items", body)])
    if res.output is None or res.blocked:
        return None
    by_n = {j.n: j for j in res.output.items}
    if len(by_n) != len(res.output.items) or set(by_n) != set(range(len(items))):
        return None
    return [by_n[n] for n in range(len(items))]


def _candidates(items: list[P4Item], judged: list[P5Item], scout: ScoutTools) -> list[Candidate]:
    out = []
    for item, j in zip(items, judged, strict=True):
        urls = scout.resolve(item.evidence_ids)  # only pages this run issued count
        out.append(Candidate(
            topic=item.topic, angle=item.angle, demand_ref=item.demand_ref, evidence_urls=tuple(urls),
            timing=item.timing, source_gap=item.source_gap or not urls,
            injection_suspected=item.injection_suspected, duplicate_of=item.is_duplicate_of,
            ethics_precheck=j.ethics_precheck,
            evidence_check=j.evidence_check, constructive_fit=j.constructive_fit,
            persona_fit=j.persona_fit, anchor_fit=j.anchor_fit, risk_lane=j.risk_lane,
            explanation=j.explanation or item.explanation,
        ))  # fmt: skip
    return out


def run_week(
    channel: str, today: dt.date, client: ModelClient, book: IdeaBook, src: Sources,
    seeds: list[str] | None = None,
) -> WeekReport:  # fmt: skip
    monday = monday_of(today)
    report = WeekReport(channel, monday)
    book.expire(before=monday)  # last week's leave the Digest even if this run stops
    if book.week(channel, monday, shown_only=False):
        report.blocked = "this week's ideas are already in the Digest"  # never a second set
        return report
    try:
        signals = src.demand(channel, today)
    except dm.DemandUnavailableError as e:
        signals = []
        report.gaps.append(f"demand: {e}")
    stories, failed = src.feeds(channel, today)
    report.gaps += [f"feed {f}" for f in failed]
    memory = book.remembered(channel)
    scout = ScoutTools(channel, stories, src.wiki_search, src.fetch_page)
    found = run_loop(client, spec("P4"), _p4_message(channel, signals, memory, seeds or []),
                     P4Output, scout.tools())  # fmt: skip
    report.depth2_attempts = scout.depth2_attempts
    if found.output is None or found.blocked:
        report.blocked = f"trend research: {found.reason}"
        return report
    items = found.output.items[:MAX_ITEMS]
    judged = _score(client, channel, items) if items else []
    if judged is None:
        report.blocked = "idea scoring did not return one judgement per candidate"
        return report
    s = get_settings()
    ideas = build_week(channel, monday, _candidates(items, judged, scout),
                       {x.signal_id: x for x in signals}, memory,
                       s.ideas_per_channel, s.evergreen_share)  # fmt: skip
    book.add(ideas)
    report.shown = sum(i.status == IdeaStatus.SHOWN for i in ideas)
    report.held = len(ideas) - report.shown
    _log.info(report.line())
    return report
