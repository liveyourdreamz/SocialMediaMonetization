"""The post pipeline as a LangGraph state machine (D20).

brief -> draft -> critic -> adapt -> gates -> render -> awaiting_approval
Any agent that fails closed parks the post (status parked) and the graph ends; a
human decides. The state is the post record plus the sources; each run uses an
in-memory checkpointer, and the post record in Postgres is the durable state (the job
queue, M1 step 3, resumes a post at its step from that record).

The four gates run in one node here; they are independent and will be fanned out
once the renderer is real and gate latency matters.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from cap.agents import AgentBlockedError, adapt, critique, critique_text, draft
from cap.config import get_settings
from cap.domain.card import prepare_card
from cap.gates import (
    HistoryIndex,
    Outcome,
    check_disclosure,
    check_ethics,
    check_facts,
    check_originality,
    confirmed_sources,
    decide,
    default_history,
)
from cap.models import ModelClient, SourceDoc
from cap.render.package import render_stage
from cap.schema.post_record import OriginalityGate, Platform, PostRecord, PostStatus


class PipelineState(TypedDict, total=False):
    record: dict[str, Any]
    sources: list[dict[str, Any]]
    parked_reason: str | None


@dataclass
class PipelineDeps:
    client: ModelClient
    # approved posts the originality gate compares against; written at approval by
    # cap.gates.record_approved. Default: <data_dir>/history.jsonl
    history: HistoryIndex = field(default_factory=default_history)
    anchor_elements: dict[str, list[str]] = field(default_factory=dict)  # channel -> elements
    character_blocks: dict[str, str] = field(default_factory=dict)  # channel -> text
    render_dir: Path = field(default_factory=lambda: Path("renders"))
    test_logs: dict[str, str] = field(default_factory=dict)  # post_id -> logged test
    checkpointer: Any | None = None  # built once at app start; MemorySaver if None
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


def _rec(state: PipelineState) -> PostRecord:
    return PostRecord.model_validate(state["record"])


def _put(record: PostRecord, **extra: Any) -> PipelineState:
    out: PipelineState = {"record": record.model_dump(mode="json", by_alias=True)}
    out.update(extra)  # type: ignore[typeddict-item]
    return out


def build_graph(deps: PipelineDeps):  # type: ignore[no-untyped-def]
    def n_draft(state: PipelineState) -> PipelineState:
        record = _rec(state)
        record.status = PostStatus.DRAFTING
        _progress(deps, record)
        try:
            record = draft(
                record,
                deps.client,
                _sources(state),
                deps.character_blocks.get(record.channel_id.value, ""),
                test_log=deps.test_logs.get(record.post_id, ""),
            )
        except AgentBlockedError as e:
            return _park(record, str(e))
        return _put(record)

    def n_critic(state: PipelineState) -> PipelineState:
        record = _rec(state)
        block = deps.character_blocks.get(record.channel_id.value, "")
        try:
            record, accepted = critique(record, deps.client, block)
        except AgentBlockedError as e:
            return _park(record, str(e))
        if not accepted:
            # one revision pass (PRD agent 5): the drafter sees the critique this time
            try:
                record = draft(
                    record,
                    deps.client,
                    _sources(state),
                    block,
                    test_log=deps.test_logs.get(record.post_id, ""),
                    critique=critique_text(record),
                )
            except AgentBlockedError as e:
                return _park(record, str(e))
        return _put(record)

    def n_adapt(state: PipelineState) -> PipelineState:
        record = _rec(state)
        try:
            record = adapt(record, deps.client, [Platform.YOUTUBE_LONG, Platform.YOUTUBE_SHORT])
        except AgentBlockedError as e:
            return _park(record, str(e))
        return _put(record)

    def n_gates(state: PipelineState) -> PipelineState:
        record = _rec(state)
        record.status = PostStatus.GATED
        _progress(deps, record)
        sources = _sources(state)
        if deps.research is not None:
            found, lines = deps.research(record)
            for ln in lines:
                record.log(ln)
            have = {s.url for s in sources}
            sources += [
                SourceDoc(
                    url=d["url"],
                    body=d["body"],
                    fetched_at=d.get("fetched_at", ""),
                    page_date=d.get("page_date"),
                )
                for d in found
                if d["url"] not in have
            ]
        # facts first (D41): the claims are checked, and only links that confirmed a
        # claim become the source list that R-12 and the published description use
        try:
            facts, injection = check_facts(
                record, deps.client, sources, test_log=deps.test_logs.get(record.post_id, "")
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
        if verdict.outcome == Outcome.REJECT:
            record.status = PostStatus.REJECTED
            record.stop("checks", "gates failed: " + "; ".join(verdict.reasons))
            record.log("gates failed: " + "; ".join(verdict.reasons))
            return _put(record, parked_reason="gates failed")
        # D43: what a gate could not settle goes to the card for Hemant to decide
        for q in verdict.questions:
            if q not in record.flags.needs_decision:
                record.flags.needs_decision.append(q)
        if record.flags.needs_decision:
            record.log(
                f"{len(record.flags.needs_decision)} question(s) for you on the card: "
                + "; ".join(n.split(":")[0] for n in record.flags.needs_decision)
            )
        return _put(record)

    def n_render(state: PipelineState) -> PipelineState:
        """The REVIEW render (review C1): what the card plays, the opening line a
        visible placeholder. The final render runs after approval (cap.approval.
        release_job) and is what the token binds."""
        record = _rec(state)
        record.status = PostStatus.RENDERING
        _progress(deps, record)
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
        return _put(record)

    def route_after(state: PipelineState) -> str:
        return END if state.get("parked_reason") else "next"

    g = StateGraph(PipelineState)
    g.add_node("draft", n_draft)
    g.add_node("critic", n_critic)
    g.add_node("adapt", n_adapt)
    g.add_node("gates", n_gates)
    g.add_node("render", n_render)
    g.set_entry_point("draft")
    g.add_conditional_edges("draft", route_after, {END: END, "next": "critic"})
    g.add_conditional_edges("critic", route_after, {END: END, "next": "adapt"})
    g.add_conditional_edges("adapt", route_after, {END: END, "next": "gates"})
    g.add_conditional_edges("gates", route_after, {END: END, "next": "render"})
    g.add_edge("render", END)
    return g


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


def _sources(state: PipelineState) -> list[SourceDoc]:
    return [
        SourceDoc(
            url=s["url"],
            body=s["body"],
            fetched_at=s.get("fetched_at", ""),
            page_date=s.get("page_date"),
        )
        for s in state.get("sources", [])
    ]


_STEP_FOR_STATUS = {
    PostStatus.DRAFTING: "draft",
    PostStatus.GATED: "checks",
    PostStatus.RENDERING: "video",
}


def _park(record: PostRecord, reason: str) -> PipelineState:
    record.stop(_STEP_FOR_STATUS.get(record.status, "draft"), reason)
    record.status = PostStatus.PARKED
    record.log(f"parked: {reason}")
    return _put(record, parked_reason=reason)


def compile_pipeline(deps: PipelineDeps, checkpointer: Any | None = None):  # type: ignore[no-untyped-def]
    graph = build_graph(deps)
    if checkpointer is None:
        from langgraph.checkpoint.memory import MemorySaver

        checkpointer = MemorySaver()
    return graph.compile(checkpointer=checkpointer)


def run_post(
    record: PostRecord,
    sources: list[dict[str, Any]],
    deps: PipelineDeps,
    checkpointer: Any | None = None,
) -> PostRecord:
    app = compile_pipeline(deps, checkpointer or deps.checkpointer)
    config = {"configurable": {"thread_id": record.post_id}}
    final = app.invoke(
        {"record": record.model_dump(mode="json"), "sources": sources}, config=config
    )
    return PostRecord.model_validate(final["record"])
