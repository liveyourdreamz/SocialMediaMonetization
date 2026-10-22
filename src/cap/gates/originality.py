"""Gate 7: originality and anchor check (PRD 4.12, rulebook R-13). Code only.

Compares the post against the channel group's recent history and checks the character
anchor is present. The similarity backend is pluggable: the Foundation default is a
deterministic lexical measure so the gate runs without any service. It catches
verbatim and near-verbatim reuse only; a plain paraphrase (scenario S4) is a known
miss until the embedding backend lands before the week-6 milestone.
Thresholds come from the rulebook's R-13 params, the one source of truth for them.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from math import sqrt
from typing import Protocol

from cap.rulebook import load_rulebook
from cap.schema.post_record import OriginalityGate, PostRecord

_WORD = re.compile(r"\w+", re.UNICODE)  # Arabic scripts tokenise too


def _tokens(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def cosine_similarity(a: str, b: str) -> float:
    """Bag-of-words cosine on 1- and 2-grams. Deterministic, dependency-free; the
    Foundation stand-in for an embedding model."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    ga = Counter(ta) + Counter(zip(ta, ta[1:], strict=False))
    gb = Counter(tb) + Counter(zip(tb, tb[1:], strict=False))
    dot = sum(ga[k] * gb[k] for k in ga.keys() & gb.keys())
    na, nb = sqrt(sum(v * v for v in ga.values())), sqrt(sum(v * v for v in gb.values()))
    return dot / (na * nb) if na and nb else 0.0


def skeleton_share(script: str, history: list[str]) -> float:
    """Share of the script's sentences whose opening three words also open a sentence in
    the history: a cheap proxy for a reused template skeleton (R-13 'templated output')."""

    def openers(text: str) -> set[tuple[str, ...]]:
        out = set()
        for sent in re.split(r"(?<=[.!?])\s+", text):
            t = _tokens(sent)
            if len(t) >= 3:
                out.add(tuple(t[:3]))
        return out

    mine = openers(script)
    if not mine:
        return 0.0
    seen: set[tuple[str, ...]] = set()
    for h in history:
        seen |= openers(h)
    return len(mine & seen) / len(mine)


class HistoryIndex(Protocol):
    """Recent approved posts for a channel group. Canaries never enter it (D23)."""

    def recent(self, channel_id: str, days: int) -> list[tuple[str, str]]:
        """Return (post_id, script_text) pairs."""


@dataclass
class InMemoryHistory:
    """History across all channels (PRD 4.12 compares against every channel)."""

    posts: list[tuple[str, str, str, datetime]] = field(default_factory=list)

    def recent(self, channel_id: str, days: int) -> list[tuple[str, str]]:
        cutoff = datetime.now(UTC) - timedelta(days=days)
        return [(pid, script) for pid, _ch, script, at in self.posts if at >= cutoff]

    def add(self, record: PostRecord) -> None:
        if record.flags.canary:
            return  # D23: canaries never enter the history index
        self.posts.append(
            (record.post_id, record.channel_id.value, record.draft.script, record.created_at)
        )


_ARABIC_MARKS = re.compile(r"[\u064B-\u065F\u0670\u0640]")  # harakat, dagger alif, tatweel


def _plain(text: str) -> str:
    """Lower case without Arabic vowel marks: "مِنَ المَدار" and "فَلَك" match the
    anchors "من المدار" and "فلك" (22 Oct: a marked Arabic post would have failed)."""
    return _ARABIC_MARKS.sub("", text.lower())


def check_originality(
    record: PostRecord,
    history: HistoryIndex,
    anchor_elements: list[str],
    require_anchor: bool = True,
) -> OriginalityGate:
    """`require_anchor=False` for a platform cut: the anchor is a property of the post
    (checked on the draft), the similarity check applies to every cut."""
    p = load_rulebook().params("R-13")
    text = f"{record.draft.title}\n{record.draft.script}"
    best, nearest = 0.0, None
    past = history.recent(record.channel_id.value, int(p["history_days"]))
    for pid, script in past:
        sim = cosine_similarity(text, script)
        if sim > best:
            best, nearest = sim, pid
    share = skeleton_share(record.draft.script, [s for _, s in past])
    lowered = _plain(text)
    anchor_present = bool(anchor_elements) and any(
        re.search(r"\b" + re.escape(_plain(a)) + r"\b", lowered) for a in anchor_elements
    )
    footage_ok = record.media_manifest.third_party_footage_share <= float(
        p["third_party_footage_cap"]
    )
    passed = (
        best < float(p["similarity_block"])
        and share <= float(p["skeleton_cap"])
        and footage_ok
        and (anchor_present or not require_anchor)
    )
    return OriginalityGate(
        max_similarity=round(best, 4),
        nearest_post_id=nearest,
        anchor_present=anchor_present,
        skeleton_share=round(share, 4),
        **{"pass": passed},
    )
