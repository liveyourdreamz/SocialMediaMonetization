"""Gate 7: originality and anchor check (PRD 4.12, rulebook R-13). Code only.

Compares the post against the recent post history (cap.gates.history: approved posts
of every channel, D23 exclusions) and checks the character anchor is present. The similarity backend is pluggable: the Foundation default is a
deterministic lexical measure so the gate runs without any service. It catches
verbatim and near-verbatim reuse only; a plain paraphrase (scenario S4) is a known
miss until the embedding backend lands before the week-6 milestone.
Thresholds come from the rulebook's R-13 params, the one source of truth for them.
"""

from __future__ import annotations

import re
from collections import Counter
from math import sqrt

from cap.gates.history import HistoryIndex
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


# Openers made only of these say nothing about a template ("and that is the", "it is
# one of"); counting them failed ordinary scripts as the history grew.
_FILLER = frozenset(
    "a an the and but or so it its it's is was are were this that these those there "
    "here what which who how why when where of in on at to from by for with as be has "
    "have had not no you your we our they their i my he she his her if then than "
    "one all some more most just can will do does did about into up out only also very "
    "في من على إلى عن هذا هذه ذلك تلك ما ماذا لماذا كيف و أن إن هو هي هم كان كانت".split()
)
OPENER_WORDS = 4


def skeleton_share(script: str, history: list[str], exclude: tuple[str, ...] = ()) -> float:
    """Share of the script's sentences whose opening words also open a sentence in the
    history: a cheap proxy for a reused template skeleton (R-13 'templated output').
    The character's signature line (`exclude`) is the same in every post on purpose and
    is not counted (26 Oct: "From orbit, here's what I see." and two common openers
    failed a Falak post at 0.41 similarity)."""
    skip = {tuple(_tokens(e)) for e in exclude if _tokens(e)}

    def openers(text: str) -> set[tuple[str, ...]]:
        out = set()
        for sent in re.split(r"(?<=[.!?؟])\s+", text):
            t = _tokens(sent)
            if tuple(t) in skip or len(t) < OPENER_WORDS:
                continue
            head = tuple(t[:OPENER_WORDS])
            if not set(head) <= _FILLER:
                out.add(head)
        return out

    mine = openers(script)
    if not mine:
        return 0.0
    seen: set[tuple[str, ...]] = set()
    for h in history:
        seen |= openers(h)
    return len(mine & seen) / len(mine)


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
    from cap.characters import OPENERS

    signature = (OPENERS.get(record.channel_id.value, ""),)
    share = skeleton_share(record.draft.script, [s for _, s in past], signature)
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
