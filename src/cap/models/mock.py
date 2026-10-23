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

    A fixture is a dict of the prompt's own fields (envelope fields are filled in; a
    fixture cannot override prompt_id or prompt_version), a (raw_text, stop_reason)
    tuple to exercise the refusal / max_tokens / error paths, or a callable(call)
    returning either. A missing fixture is a blocked output: the fail-closed path.
    """

    def __init__(self, fixtures: dict[tuple[str, str], FixtureValue] | None = None) -> None:
        self.fixtures = fixtures or {}
        self.calls: list[ModelCall] = []

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
