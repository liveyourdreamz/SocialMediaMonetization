"""Seeding canaries (agent 16, Prompt Pack P14, D23, D34; redesigned after review R2).

Every brief reaches the queue the same way: Hemant files it on Operator or picks an idea
in the Digest, and `file_brief` saves it and queues its first job. When a canary is due
(cap.domain.canary: the drawn gap since the last canary has passed, none is waiting, the
queue is not paused), that brief becomes the canary instead: the same topic, drafted,
with one fault planted by P14, gated, rendered and queued looking exactly like the card
Hemant expects from what he filed. Nothing marks it before the verdict, because there
is nothing to mark: it is the post he asked for.

Once the verdict is recorded (caught or missed), `refile_clean` files the brief again,
unchanged, so the clean post still gets made. The publisher refuses every canary (D23);
the card scores it on the blind verdict.

The decision runs under the store's named lock, so two briefs filed at the same moment,
by two threads or two processes, cannot both become canaries (review R3).
"""

from __future__ import annotations

import random
import shutil
from pathlib import Path
from typing import Any

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
from cap.domain.cost import charge, metering
from cap.jobs import Job, JobKind
from cap.schema.enums import FaultType
from cap.schema.post_record import CanaryRefile, Platform, PostRecord, PostStatus
from cap.store import PostStore

LOCK = "canary"
WAITING = (PostStatus.BRIEFED, PostStatus.DRAFTING, PostStatus.GATED, PostStatus.RENDERING,
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


def canary_due(store: PostStore, rng: random.Random | None = None) -> bool:
    """Whether the next brief filed should be a canary. Draws the gap when none is set.
    Call under `store.exclusive(LOCK)`."""
    if store.paused or canary_waiting(store):
        return False
    cards = closed_cards(store)
    gap = store.state.canary_gap
    if not gap:
        gap = draw_gap(rate(cards), rng or random.SystemRandom())
        store.update_state(lambda s: setattr(s, "canary_gap", gap))
    return cards_since_last_canary(cards) >= gap


def file_brief(
    store: PostStore,
    record: PostRecord,
    payload: dict[str, Any],
    *,
    idea_id: str = "",
    rng: random.Random | None = None,
) -> JobKind:
    """Save a brief Hemant filed or picked and queue its first job: BRIEF for a clean
    post, CANARY when one is due. Returns the job kind queued."""
    with store.exclusive(LOCK):
        if record.flags.tour or not canary_due(store, rng):
            store.file(record, JobKind.BRIEF, payload)
            return JobKind.BRIEF
        record.flags.canary = True  # set by code, never by a model (D23)
        record.flags.canary_fault_type = next_fault(past_canaries(store))
        record.flags.canary_refile = CanaryRefile(
            urls=[str(u) for u in payload.get("urls", [])],
            test_log=str(payload.get("test_log", "")),
            idea_id=idea_id,
        )
        store.file(record, JobKind.CANARY, payload)
        # the next gap is drawn when the next brief is filed
        store.update_state(lambda s: setattr(s, "canary_gap", 0))
    return JobKind.CANARY


def refile_clean(store: PostStore, canary: PostRecord) -> PostRecord | None:
    """After a canary's verdict: file the brief it replaced again, clean. Returns the
    new post, or None when the record was not a canary that replaced a brief or its
    brief was already filed again. `canary` must be the saved record; it is saved again
    with the refile cleared before the clean brief is filed, so it happens once."""
    refile = canary.flags.canary_refile
    if not canary.flags.canary or refile is None or not canary.approval.decided_at:
        return None
    from cap.demo.seed import new_brief

    canary.flags.canary_refile = None
    store.put(canary)  # PostConflictError if another request got here first

    clean = new_brief(canary.channel_id, canary.brief.topic)
    clean.brief = canary.brief.model_copy(deep=True)
    media = Path(get_settings().data_dir) / "media"
    if (media / canary.post_id).is_dir():  # screenshots attached to the brief
        shutil.copytree(media / canary.post_id, media / clean.post_id, dirs_exist_ok=True)
    clean.log("filed again after the check post on this topic; drafted from the start")
    store.file(clean, JobKind.BRIEF, {"urls": list(refile.urls), "test_log": refile.test_log})
    store.audit.append(
        "brief filed again after a canary",
        post_id=clean.post_id,
        detail={"canary": canary.post_id, "idea": refile.idea_id},
    )
    return clean


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
    """The worker's handler for a CANARY job: draft from what Hemant filed, plant one
    fault, gate, render, queue. Every model call is charged to the post (D44)."""
    from cap.agents import adapt, write_canary
    from cap.demo.seed import _setup

    record = store.get(job.post_id)
    test_log = str(job.payload.get("test_log", ""))
    with metering() as spent:
        deps, sources = _setup(store, record, test_log, urls=list(job.payload.get("urls", [])))
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
    charge(record, spent, get_settings().post_budget_usd)  # P14 and the second adapt
    record = pipeline.checks(record, docs, deps)
    record.flags.canary_gates_caught = _gates_caught(record)
    if pipeline.stopped(record):  # a gate caught it: recorded, and the card goes on (P14)
        record.status = PostStatus.GATED
        record.flags.stopped_at, record.flags.stop_reason = "", ""
    caught = ", ".join(record.flags.canary_gates_caught) or "none (gate-blind)"
    record.log(f"canary gates caught: {caught}")
    out = pipeline.render(record, deps)
    store.put(out)
