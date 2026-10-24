"""Seeding canaries into the queue (agent 16, Prompt Pack P14, D23, D34).

The worker's clock asks `schedule_canary` every minute. A canary is seeded when the
reviewer has closed as many cards since the last canary as the drawn gap
(cap.domain.canary), no canary is already waiting, and the queue is not paused.

The base is an idea from this week's Digest that the reviewer has not picked: rank 2
or lower, on a topic not posted or picked in the last 90 days. It is taken out of the
Digest, so the reviewer never sees the same topic as both an idea and a card. The
CANARY job then drafts it like any brief, has P14 plant one fault, runs every gate
with the canary flag set (what the gates caught is recorded, nothing stops the card),
renders it, and queues it looking exactly like a clean post. The publisher refuses
every canary (D23); the card scores it on the blind verdict.
"""

from __future__ import annotations

import random

from cap import pipeline
from cap.config import get_settings
from cap.domain.canary import (
    ClosedCard,
    PastCanary,
    cards_since_last_canary,
    draw_gap,
    next_fault,
    rate,
)
from cap.ideabook import IdeaDecidedError
from cap.ideas.rank import same_topic
from cap.ideas.week import monday_of
from cap.jobs import Job, JobKind
from cap.schema.enums import FaultType
from cap.schema.ideas import Idea, IdeaStatus
from cap.schema.post_record import Platform, PostRecord, PostStatus
from cap.store import PostStore

MEMORY_DAYS = 90
WAITING = (PostStatus.DRAFTING, PostStatus.GATED, PostStatus.RENDERING,
           PostStatus.AWAITING_APPROVAL)  # fmt: skip
GATE_NAMES = ("originality", "ethics", "disclosure", "facts")


def closed_cards(store: PostStore) -> list[ClosedCard]:
    """Every card a reviewer closed, oldest first (the tour never counts)."""
    done = [p for p in store.all() if p.approval.decided_at and not p.flags.tour]
    done.sort(key=lambda p: p.approval.decided_at)  # type: ignore[arg-type, return-value]
    return [ClosedCard(p.flags.canary, p.flags.canary_outcome) for p in done]


def past_canaries(store: PostStore) -> list[PastCanary]:
    made = [p for p in store.all() if p.flags.canary and p.flags.canary_fault_type]
    made.sort(key=lambda p: p.created_at)
    return [PastCanary(p.flags.canary_fault_type, not p.flags.canary_gates_caught)  # type: ignore[arg-type]
            for p in made]  # fmt: skip


def canary_waiting(store: PostStore) -> bool:
    return any(p.flags.canary and p.status in WAITING for p in store.all())


def _recent_topics(store: PostStore, channel: str) -> list[str]:
    import datetime as dt

    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=MEMORY_DAYS)
    posts = [
        p.brief.topic
        for p in store.all()
        if p.channel_id.value == channel and p.created_at >= since
    ]
    picked = [i.topic_edit or i.topic for i in store.ideas.recent(channel)
              if i.status == IdeaStatus.PICKED]  # fmt: skip
    return posts + picked


def pick_base(store: PostStore, today=None) -> Idea | None:  # type: ignore[no-untyped-def]
    """An unpicked idea from this week's Digest, rank 2 or lower, on a fresh topic. The
    channel with the fewest canaries goes first."""
    import datetime as dt

    monday = monday_of(today or dt.datetime.now(dt.UTC).date())
    made = {ch: 0 for ch in get_settings().channels}
    for p in store.all():
        if p.flags.canary and p.channel_id.value in made:
            made[p.channel_id.value] += 1
    for ch in sorted(made, key=lambda c: made[c]):
        recent = _recent_topics(store, ch)
        for idea in store.ideas.week(ch, monday):
            if idea.status != IdeaStatus.SHOWN or idea.rank < 2:
                continue
            if any(same_topic(idea.topic, t) for t in recent):
                continue
            return idea
    return None


def schedule_canary(store: PostStore, rng: random.Random | None = None) -> str | None:
    """Seed a canary if one is due; the new post's id, or None."""
    if store.paused or canary_waiting(store):
        return None
    cards = closed_cards(store)
    if not store.state.canary_gap:
        store.state.canary_gap = draw_gap(rate(cards), rng or random.SystemRandom())
        store.save_state()
    if cards_since_last_canary(cards) < store.state.canary_gap:
        return None
    base = pick_base(store)
    if base is None:
        return None  # no idea to build on yet; the next tick tries again
    try:
        store.ideas.take_for_canary(base.idea_id)
    except IdeaDecidedError:
        return None
    from cap.demo.seed import new_brief

    record = new_brief(base.channel, base.topic)
    record.flags.canary = True  # set by code, never by a model (D23)
    record.flags.canary_fault_type = next_fault(past_canaries(store))
    store.put(record)
    store.jobs.enqueue(JobKind.CANARY, record.post_id, {"idea": base.idea_id})
    store.state.canary_gap = 0  # the next gap is drawn after this one is closed
    store.save_state()
    store.audit.append("canary seeded", post_id=record.post_id,
                       detail={"fault": record.flags.canary_fault_type.value})  # fmt: skip
    return record.post_id


def _gates_caught(record: PostRecord) -> list[str]:
    g = record.gates
    failed = {
        "originality": not g.originality.passed,
        "ethics": g.ethics.verdict.value != "pass",
        "disclosure": not g.disclosure.passed,
        "facts": not g.facts.passed,
    }
    return [name for name in GATE_NAMES if failed[name]]


def run_canary_job(store: PostStore, job: Job) -> None:
    """The worker's handler for a CANARY job: draft, plant, gate, render, queue."""
    from cap.agents import adapt, write_canary
    from cap.demo.seed import _setup

    record = store.get(job.post_id)
    deps, sources = _setup(store, record, "")
    docs = pipeline.as_sources(sources)
    record = pipeline.write(record, docs, deps)
    if pipeline.stopped(record):
        raise RuntimeError(f"the base draft stopped: {record.flags.stop_reason}")
    base = record.draft.model_dump(include={"title", "script", "caption", "on_screen_text",
                                            "thumbnail_text", "shot_list"})  # fmt: skip
    fault = record.flags.canary_fault_type or FaultType.UNSOURCED_NUMBER
    record = write_canary(record, deps.client, fault, base,
                          deps.character_blocks.get(record.channel_id.value, ""))  # fmt: skip
    # the platform cuts are made again from the canary's own script, as for any post
    record = adapt(record, deps.client, [Platform.YOUTUBE_LONG, Platform.YOUTUBE_SHORT])
    record = pipeline.checks(record, docs, deps)
    record.flags.canary_gates_caught = _gates_caught(record)
    if pipeline.stopped(record):  # a gate caught it: recorded, and the card goes on (P14)
        record.status = PostStatus.GATED
        record.flags.stopped_at, record.flags.stop_reason = "", ""
    record.log(
        f"canary gates caught: {', '.join(record.flags.canary_gates_caught) or 'none (gate-blind)'}"
    )
    out = pipeline.render(record, deps)
    store.put(out)
