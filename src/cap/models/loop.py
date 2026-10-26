"""A bounded tool loop on the pipeline's own client (D45, ADR-013): the model may call
the tools code gives it, code runs them, and the loop ends with one structured output.

Used by trend research (P4), the only prompt that acts in steps. The bound is in code:
`max_turns` model calls at most, and the last turn has tools switched off so the model
has to write its output. Every tool result came from outside the pipeline, so it is
wrapped in this run's nonce-keyed tag exactly like fetched sources (Prompt Pack
section 2); a tool can never put trusted text in front of the model. Every turn is
metered (D44). A tool that raises returns its error to the model, never stops the run.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from cap.config import get_settings
from cap.domain.cost import note
from cap.models.base import (
    MAX_OUTPUT_TOKENS,
    MAX_TOKENS,
    Completion,
    Envelope,
    ModelCall,
    ModelResult,
    _blocked_output,
    _parse,
    _result,
    _system_blocks,
)
from cap.prompts import PromptSpec, new_nonce, untrusted


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    run: Callable[[dict[str, Any]], str]  # returns text from outside: wrapped by the loop

    def declaration(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


@dataclass(frozen=True)
class ToolUse:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class Step:
    """One model reply inside a loop: tool calls to run, or the final text."""

    completion: Completion
    tool_uses: list[ToolUse] = field(default_factory=list)
    assistant_content: list[dict[str, Any]] = field(default_factory=list)  # echoed back as-is


class LoopBackend(Protocol):
    def _step(
        self, call: ModelCall, messages: list[dict[str, Any]], tools: list[Tool], final: bool
    ) -> Step: ...


def _run_tool(tools: dict[str, Tool], use: ToolUse) -> tuple[str, bool]:
    tool = tools.get(use.name)
    if tool is None:
        return f"no tool named {use.name}", True
    try:
        return tool.run(dict(use.input)), False
    except Exception as e:  # noqa: BLE001 - the model hears the error; the run goes on
        return f"{type(e).__name__}: {str(e)[:300]}", True


def tool_results(tools: dict[str, Tool], uses: list[ToolUse], nonce: str) -> list[dict[str, Any]]:
    """The user turn answering the model's tool calls, every body wrapped as untrusted."""
    blocks = []
    for use in uses:
        text, is_error = _run_tool(tools, use)
        blocks.append(
            {
                "type": "tool_result",
                "tool_use_id": use.id,
                "content": [{"type": "text", "text": untrusted(text, nonce)}],
                "is_error": is_error,
            }
        )
    return blocks


def run_loop[T: Envelope](
    backend: LoopBackend,
    spec: PromptSpec,
    user: str | Callable[[str], str],
    output_model: type[T],
    tools: list[Tool],
    *,
    max_turns: int = 20,
    static_blocks: list[str] | None = None,
) -> ModelResult[T]:
    nonce = new_nonce()
    if callable(user):  # the message wraps outside text in this run's nonce tag
        user = user(nonce)
    call = ModelCall(
        spec=spec,
        nonce=nonce,
        system=_system_blocks(spec, nonce, static_blocks or []),
        user=user,
        user_tail="",
        output_model=output_model,
        sources=[],
        max_tokens=MAX_TOKENS[spec.effort],
        log_thinking=get_settings().log_thinking,
    )
    by_name = {t.name: t for t in tools}
    messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
    last = Completion(raw="", stop_reason="error")
    for turn in range(1, max_turns + 1):
        step = backend._step(call, messages, tools, final=turn == max_turns)
        last = step.completion
        if last.usage:
            note(f"{spec.prompt_id} turn {turn}", last.model or spec.model, last.usage)
        if last.stop_reason == "refusal":
            reason = f"model_refusal:{last.stop_category or 'unspecified'}"
            return _result(_blocked_output(output_model, spec, reason), last, turn)
        if last.stop_reason == "error":
            return _result(None, last, turn, last.error or "request failed")
        if last.stop_reason == "max_tokens" and call.max_tokens < MAX_OUTPUT_TOKENS:
            # Pack section 1, as for single calls: the same turn again with more room
            call.max_tokens = min(call.max_tokens * 4, MAX_OUTPUT_TOKENS)
            continue
        if step.tool_uses and turn < max_turns:
            messages.append({"role": "assistant", "content": step.assistant_content})
            messages.append(
                {"role": "user", "content": tool_results(by_name, step.tool_uses, nonce)}
            )
            continue
        parsed, why = _parse(last.raw, output_model, spec)
        return _result(parsed, last, turn, why)
    return _result(None, last, max_turns, "turn limit reached without an output")
