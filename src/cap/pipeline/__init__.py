"""The post pipeline as a LangGraph state machine (D20).

brief -> draft -> critic -> adapt -> gates -> render -> awaiting_approval
Any agent that fails closed parks the post (status parked) and the graph ends; a
human decides. The state is the post record plus the sources; the checkpointer is
SQLite locally and Postgres in production, chosen by settings.

The four gates run in one node here; they are independent and will be fanned out
once the renderer is real and gate latency matters.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from cap.agents import AgentBlockedError, adapt, critique, critique_text, draft
from cap.approval.card import prepare_card
from cap.config import get_settings
from cap.gates import (
    InMemoryHistory,
    check_disclosure,
    check_ethics,
    check_facts,
    check_originality,
)
from cap.models import ModelClient, SourceDoc
from cap.schema.post_record import OriginalityGate, Platform, PostRecord, PostStatus


class PipelineState(TypedDict, total=False):
    record: dict[str, Any]
    sources: list[dict[str, Any]]
    parked_reason: str | None


@dataclass
class PipelineDeps:
    client: ModelClient
    history: InMemoryHistory = field(default_factory=InMemoryHistory)
    anchor_elements: dict[str, list[str]] = field(default_factory=dict)  # channel -> elements
    character_blocks: dict[str, str] = field(default_factory=dict)  # channel -> text
    render_dir: Path = field(default_factory=lambda: Path("renders"))
    test_logs: dict[str, str] = field(default_factory=dict)  # post_id -> logged test
    checkpointer: Any | None = None  # built once at app start; MemorySaver if None


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
        sources = _sources(state)
        anchors = deps.anchor_elements.get(record.channel_id.value, [])
        record.gates.originality = _originality_over_variants(record, deps, anchors)
        record.gates.ethics = check_ethics(record, deps.client)
        record.gates.disclosure = check_disclosure(record, deps.client)
        try:
            facts, injection = check_facts(
                record, deps.client, sources, test_log=deps.test_logs.get(record.post_id, "")
            )
        except AgentBlockedError as e:
            return _park(record, str(e))
        record.gates.facts = facts
        if injection:
            record.flags.injection_suspected = True
            return _park(record, "injection suspected in fetched content; parked for review")
        if record.gates.ethics.verdict.value == "escalate":
            return _park(record, "ethics gate escalated")
        if record.gates.disclosure.mfk_band in ("doubtful", "unscored"):
            return _park(
                record, f"made-for-kids {record.gates.disclosure.mfk_band}: Hemant decides"
            )
        if not record.gates.all_passed:
            record.status = PostStatus.REJECTED
            record.log("gates failed: " + ", ".join(_failed_gates(record)))
            return _put(record, parked_reason="gates failed")
        return _put(record)

    def n_render(state: PipelineState) -> PipelineState:
        record = _rec(state)
        record.status = PostStatus.RENDERING
        deps.render_dir.mkdir(parents=True, exist_ok=True)
        if get_settings().renderer == "video":
            from cap.render import RenderError, render, silent_voice, speak

            voice_fn = speak if get_settings().voice == "edge" else silent_voice
            try:
                r = render(record, deps.render_dir, voice_fn=voice_fn)
            except RenderError as e:
                return _park(record, f"render failed: {e}")
            except Exception as e:  # the voice service, ffmpeg: fail closed, say why
                return _park(record, f"render failed: {type(e).__name__}: {str(e)[:200]}")
            record.asset.uri = str(r.video)
            record.asset.sha256 = r.sha256
            record.asset.short_uri = str(r.short) if r.short else ""
            record.asset.thumbnail_uri = str(r.thumbnail)
            record.asset.duration_s = round(r.duration_s, 1)
            record.asset.voice = r.voice
            record.asset.kind = "video"
            record.log(
                f"rendered {r.duration_s:.0f}s long-form{' and a Short' if r.short else ''} with voice {r.voice}"
            )
        else:
            # Foundation stand-in: a text manifest of what would be rendered. The hash
            # binding and the approval token are real either way.
            path = deps.render_dir / f"{record.post_id}.render.txt"
            content = f"{record.draft.title}\n\n{record.draft.script}\n"
            path.write_text(content, encoding="utf-8")
            record.asset.uri = str(path)
            record.asset.sha256 = hashlib.sha256(content.encode()).hexdigest()
            record.asset.kind = "text"
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


def _park(record: PostRecord, reason: str) -> PipelineState:
    record.status = PostStatus.PARKED
    record.log(f"parked: {reason}")
    return _put(record, parked_reason=reason)


def _failed_gates(record: PostRecord) -> list[str]:
    g = record.gates
    out = []
    if not g.originality.passed:
        out.append("originality")
    if g.ethics.verdict.value != "pass":
        out.append("ethics")
    if not g.disclosure.passed:
        out.append("disclosure")
    if not g.facts.passed:
        out.append("facts")
    return out


def compile_pipeline(deps: PipelineDeps, checkpointer: Any | None = None):  # type: ignore[no-untyped-def]
    graph = build_graph(deps)
    if checkpointer is None:
        from langgraph.checkpoint.memory import MemorySaver

        checkpointer = MemorySaver()
    return graph.compile(checkpointer=checkpointer)


def sqlite_checkpointer():  # type: ignore[no-untyped-def]
    """Local checkpointer (settings.checkpoint_path). Production swaps in Postgres."""
    import sqlite3

    from langgraph.checkpoint.sqlite import SqliteSaver

    conn = sqlite3.connect(get_settings().checkpoint_path, check_same_thread=False)
    return SqliteSaver(conn)


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
