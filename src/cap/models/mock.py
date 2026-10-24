"""The mock backend: answers from fixtures, so the pipeline runs without a key (D31)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from cap.prompts import pack_version

if TYPE_CHECKING:
    pass
from cap.models.base import (
    Completion,
    FixtureValue,
    ModelCall,
    ModelClient,
)


class MockClient(ModelClient):
    """Answers from fixtures keyed by (prompt_id, call) or (prompt_id, "*").

    For a tool loop (cap.models.loop) the fixture is a script, a list with one entry
    per turn: {"tool_uses": [{"name": ..., "input": {...}}, ...]} to call tools, or the
    output dict. The last entry is the output; the forced final turn jumps to it.

    A fixture is a dict of the prompt's own fields (envelope fields are filled in; a
    fixture cannot override prompt_id or prompt_version), a (raw_text, stop_reason)
    tuple to exercise the refusal / max_tokens / error paths, or a callable(call)
    returning either. A missing fixture is a blocked output: the fail-closed path.
    """

    def __init__(self, fixtures: dict[tuple[str, str], FixtureValue] | None = None) -> None:
        self.fixtures = fixtures or {}
        self.calls: list[ModelCall] = []
        self.transcripts: list[list[dict[str, Any]]] = []  # loop steps: what the model saw

    def _complete(self, call: ModelCall) -> Completion:
        self.calls.append(call)
        fx: Any = self.fixtures.get((call.spec.prompt_id, call.spec.call))
        if fx is None:
            fx = self.fixtures.get((call.spec.prompt_id, "*"))
        if callable(fx):
            fx = fx(call)  # the whole call: user, user_tail, sources, system
        if isinstance(fx, tuple):
            return Completion(raw=fx[0], stop_reason=fx[1], model="mock")
        if fx is None:
            fx = {"status": "blocked", "blocked_reason": "no fixture for this prompt in mock mode"}
        envelope = {
            "status": "ok",
            "blocked_reason": None,
            "injection_suspected": False,
            "explanation": "mock",
        }
        merged = {
            **envelope,
            **fx,
            "prompt_id": call.spec.prompt_id,
            "prompt_version": pack_version(),
        }
        return Completion(raw=json.dumps(merged), stop_reason="mock", model="mock")

    def _step(
        self, call: ModelCall, messages: list[dict[str, Any]], tools: list[Any], final: bool
    ) -> Any:
        from cap.models.loop import Step, ToolUse

        self.calls.append(call)
        self.transcripts.append([dict(m) for m in messages])
        script = self.fixtures.get((call.spec.prompt_id, call.spec.call))
        if not isinstance(script, list) or not script:
            out = {
                "status": "blocked",
                "blocked_reason": "no loop script for this prompt in mock mode",
            }
            return Step(self._output(call, out))
        turn = sum(1 for m in messages if m["role"] == "assistant")
        entry = script[-1] if final or turn >= len(script) else script[turn]
        if isinstance(entry, dict) and "tool_uses" in entry:
            uses = [
                ToolUse(f"tu_{turn}_{k}", u["name"], u.get("input", {}))
                for k, u in enumerate(entry["tool_uses"])
            ]
            content = [
                {"type": "tool_use", "id": u.id, "name": u.name, "input": u.input} for u in uses
            ]
            return Step(Completion(raw="", stop_reason="tool_use", model="mock"), uses, content)
        return Step(self._output(call, entry))

    def _output(self, call: ModelCall, fx: dict[str, Any]) -> Completion:
        merged = {
            "status": "ok",
            "blocked_reason": None,
            "injection_suspected": False,
            "explanation": "mock",
            **fx,
            "prompt_id": call.spec.prompt_id,
            "prompt_version": pack_version(),
        }
        return Completion(raw=json.dumps(merged), stop_reason="mock", model="mock")
