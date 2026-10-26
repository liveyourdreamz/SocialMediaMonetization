"""The post pipeline: three steps, brief to the approval queue (D20 as amended by D45).

    write   draft -> critic (one revision) -> adapt         status drafting
    checks  research -> facts -> originality -> ethics -> disclosure -> verdict
                                                             status gated
    render  the review cut; the card is prepared             status rendering
            -> awaiting_approval

Each step is a function from the post record to the post record, and each runs as
its own queued job (cap.worker), so a restart resumes at the step it was on. The post
record is the whole state; the sources found while writing travel to the checks step
with its job. A step that fails closed parks the post (or the verdict rejects it) and
the next step is not queued; a human decides. `run_post` runs the three in a row (the
full check and the tests).

Every model call made inside a step is charged to the post when the step ends
(cap.domain.cost, D44); a post over its budget carries on and the card says so.

The four gates run in sequence here; they are independent and can be fanned out once
gate latency matters.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cap.agents import AgentBlockedError, adapt, critique, critique_text, draft
from cap.config import get_settings
from cap.domain.card import prepare_card
from cap.domain.cost import charge, keep_on, metering
from cap.gates import (
    HistoryIndex,
    check_disclosure,
    check_ethics,
    check_facts,
    check_originality,
    confirmed_sources,
    decide,
)
from cap.models import ModelClient, SourceDoc
from cap.render.package import render_stage
from cap.schema.post_record import OriginalityGate, Platform, PostRecord, PostStatus


@dataclass
class PipelineDeps:
    client: ModelClient
    # approved posts the originality gate compares against; written at approval by
    # cap.gates.record_approved. Required, never defaulted: the caller passes the
    # store's own history, so the gate reads where approvals are written (review R1).
    history: HistoryIndex
    anchor_elements: dict[str, list[str]] = field(default_factory=dict)  # channel -> elements
    character_blocks: dict[str, str] = field(default_factory=dict)  # channel -> text
    render_dir: Path = field(default_factory=lambda: Path("renders"))
    test_logs: dict[str, str] = field(default_factory=dict)  # post_id -> logged test
    # D41: called with the drafted record; returns source dicts that confirm or refute
    # its claims, plus log lines. None = the sources given up front are all there is.
    research: Any | None = None
    # called with the record as each stage starts, so the progress page and a restart
    # both see where it is (review H4). None = nothing outside watches.
    on_progress: Any | None = None


def _progress(deps: PipelineDeps, record: PostRecord) -> None:
    if deps.on_progress is None:
        return
    saved = record.model_copy(deep=True)
    try:
        deps.on_progress(saved)
    except Exception:  # a progress save never stops the post; it is logged
        logging.getLogger("cap.pipeline").exception("progress save failed for %s", record.post_id)
        return
    record.revision = saved.revision  # the next save builds on this one


def _note(deps: PipelineDeps, record: PostRecord, line: str) -> None:
    """A line in the post's log, saved at once, so its page shows what is happening
    inside a long step (Hemant, 25 Oct: five minutes on "Writing the script" and one
    log entry)."""
    record.log(line)
    _progress(deps, record)


def _metered[**P](step: Callable[P, PostRecord]) -> Callable[P, PostRecord]:
    """Charge the model calls a step made to the record it returns; when the step
    raises, the calls travel on the error to the worker, which charges them (R9)."""

    @functools.wraps(step)
    def run(*args: P.args, **kwargs: P.kwargs) -> PostRecord:
        with metering() as spent:
            try:
                out = step(*args, **kwargs)
            except Exception as e:
                keep_on(e, spent)
                raise
        charge(out, spent, get_settings().post_budget_usd)
        return out

    return run


@_metered
def write(record: PostRecord, sources: list[SourceDoc], deps: PipelineDeps) -> PostRecord:
    """Draft, one critique (and one revision if the critic asks), then the platform
    variants. Parks the post when an agent fails closed."""
    record.status = PostStatus.DRAFTING
    _progress(deps, record)
    block = deps.character_blocks.get(record.channel_id.value, "")
    test_log = deps.test_logs.get(record.post_id, "")
    try:
        record = draft(record, deps.client, sources, block, test_log=test_log)
        _note(
            deps, record, f"script written ({len(record.draft.claims)} claims); the critic reads it"
        )
        record, accepted = critique(record, deps.client, block)
        if not accepted:
            _note(deps, record, "the critic asked for changes; rewriting the script")
            # one revision pass (PRD agent 5): the drafter sees the critique this time
            record = draft(
                record,
                deps.client,
                sources,
                block,
                test_log=test_log,
                critique=critique_text(record),
            )
            _note(deps, record, "script rewritten; writing the YouTube versions")
        else:
            _note(deps, record, "the critic accepted it; writing the YouTube versions")
        record = adapt(record, deps.client, [Platform.YOUTUBE_LONG, Platform.YOUTUBE_SHORT])
    except AgentBlockedError as e:
        return _park(record, str(e))
    return record


def research(record: PostRecord, sources: list[SourceDoc], deps: PipelineDeps) -> list[SourceDoc]:
    """D41: look for a source for each claim the draft made; returns the sources the
    checks step uses (those given up front, plus what research found)."""
    if deps.research is None:
        return sources
    found, lines = deps.research(record)
    for ln in lines:
        record.log(ln)
    have = {s.url for s in sources}
    return sources + [
        SourceDoc(
            url=d["url"],
            body=d["body"],
            fetched_at=d.get("fetched_at", ""),
            page_date=d.get("page_date"),
        )
        for d in found
        if d["url"] not in have
    ]


@_metered
def checks(record: PostRecord, sources: list[SourceDoc], deps: PipelineDeps) -> PostRecord:
    """Research, then the four gates, then the one verdict (cap.domain.verdict): pass,
    questions for the card (D43), or reject."""
    record.status = PostStatus.GATED
    _note(deps, record, "looking for a source for each claim")
    sources = research(record, sources, deps)
    # facts first (D41): the claims are checked, and only links that confirmed a
    # claim become the source list that R-12 and the published description use
    try:
        facts, injection = check_facts(
            record,
            deps.client,
            sources,
            test_log=deps.test_logs.get(record.post_id, ""),
            on_claim=lambda line: _note(deps, record, line),
        )
    except AgentBlockedError as e:
        return _park(record, str(e))
    record.gates.facts = facts
    record.draft.source_list = confirmed_sources(record)
    if injection:
        record.flags.injection_suspected = True
        return _park(record, "injection suspected in fetched content; parked for review")
    anchors = deps.anchor_elements.get(record.channel_id.value, [])
    record.gates.originality = _originality_over_variants(record, deps, anchors)
    if not record.gates.originality.passed and not record.gates.originality.anchor_present:
        # D43: the signature phrase is missing; add the character's opener and recheck
        from cap.agents import OPENING_LINE_SLOT
        from cap.characters import OPENERS

        opener = OPENERS.get(record.channel_id.value, "")
        if opener:
            rest = record.draft.script.replace(OPENING_LINE_SLOT, "").strip()
            record.draft.script = f"{OPENING_LINE_SLOT} {opener} {rest}"
            record.log(f'repaired: added the signature opener "{opener}"')
            record.gates.originality = _originality_over_variants(record, deps, anchors)
    record.gates.ethics = check_ethics(record, deps.client)
    record.gates.disclosure = check_disclosure(record, deps.client)
    # one rule decides what the four results mean together (cap.domain.verdict)
    verdict = decide(record)
    # D43, D51: every finding goes to the card; the checks never stop a post
    for q in verdict.questions:
        if q not in record.flags.needs_decision:
            record.flags.needs_decision.append(q)
    if record.flags.needs_decision:
        record.log(
            f"{len(record.flags.needs_decision)} question(s) for you on the card: "
            + "; ".join(n.split(":")[0] for n in record.flags.needs_decision)
        )
    return record


@_metered
def render(record: PostRecord, deps: PipelineDeps) -> PostRecord:
    """The REVIEW render (review C1): what the card plays, the opening line a visible
    placeholder. The final render runs after approval (cap.approval.release_job) and
    is what the token binds."""
    record.status = PostStatus.RENDERING
    _note(deps, record, "recording the voice and drawing the video")
    kind = get_settings().renderer
    try:
        r = render_stage(record, deps.render_dir, "review", kind)
    except Exception as e:  # the voice service, ffmpeg: fail closed, say why
        from cap.render import RenderError

        why = str(e) if isinstance(e, RenderError) else f"{type(e).__name__}: {str(e)[:200]}"
        return _park(record, f"render failed: {why}")
    record.asset.uri = str(r.files["long"])
    record.asset.sha256 = r.sha256
    record.asset.kind = r.kind
    if r.kind == "video":
        record.asset.short_uri = str(r.files["short"]) if "short" in r.files else ""
        record.asset.thumbnail_uri = str(r.files["thumbnail"])
        record.asset.duration_s = round(r.duration_s, 1)
        record.asset.voice = r.voice
        record.log(
            f"rendered {r.duration_s:.0f}s long-form{' and a Short' if 'short' in r.files else ''}"
            f" with voice {r.voice} (review cut)"
        )
    record.status = PostStatus.AWAITING_APPROVAL
    prepare_card(record)  # D34: spot check and reading threshold fixed at queue time
    record.log("queued for approval")
    return record


def stopped(record: PostRecord) -> bool:
    """A step parked or rejected the post: the next step does not run."""
    return record.status in (PostStatus.PARKED, PostStatus.REJECTED)


def _originality_over_variants(
    record: PostRecord, deps: PipelineDeps, anchors: list[str]
) -> OriginalityGate:
    """PRD 4.12: the Short reusing a cold open must fail too, so the check runs on the
    draft and on every variant's own text; the worst similarity is kept and the gate
    passes only if every check passes."""
    results = [check_originality(record, deps.history, anchors)]
    for v in record.variants:
        if not v.script_cut:
            continue
        probe = record.model_copy(deep=True)
        probe.draft.title, probe.draft.script = v.title, v.script_cut
        results.append(check_originality(probe, deps.history, anchors, require_anchor=False))
    worst = max(results, key=lambda g: g.max_similarity)
    worst.passed = all(g.passed for g in results)
    return worst


_STEP_FOR_STATUS = {
    PostStatus.DRAFTING: "draft",
    PostStatus.GATED: "checks",
    PostStatus.RENDERING: "video",
}


def _park(record: PostRecord, reason: str) -> PostRecord:
    record.stop(_STEP_FOR_STATUS.get(record.status, "draft"), reason)
    record.status = PostStatus.PARKED
    record.log(f"parked: {reason}")
    return record


def as_sources(docs: list[dict[str, Any]]) -> list[SourceDoc]:
    return [
        SourceDoc(
            url=s["url"],
            body=s["body"],
            fetched_at=s.get("fetched_at", ""),
            page_date=s.get("page_date"),
        )
        for s in docs
    ]


def run_post(record: PostRecord, sources: list[dict[str, Any]], deps: PipelineDeps) -> PostRecord:
    """The three steps in a row, stopping where a step parks or rejects the post."""
    docs = as_sources(sources)
    record = write(record, docs, deps)
    if stopped(record):
        return record
    record = checks(record, docs, deps)
    if stopped(record):
        return record
    return render(record, deps)
