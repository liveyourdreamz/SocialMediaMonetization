"""Model client: one place that talks to Claude, with the Prompt Pack's rules baked in.

* No sampling parameters (5.5 models reject them); `output_config.effort` only.
* Structured output: the caller passes a pydantic model; the response is validated,
  retried once, then returned as `blocked` (D28). A refusal stop reason is handled
  separately from a malformed reply.
* Every output carries the envelope fields (prompt_id, prompt_version, status,
  blocked_reason, injection_suspected, explanation); code rejects any that do not.
* The mock backend answers from fixtures keyed by prompt id, so the whole pipeline
  runs without a key. The Anthropic backend is used in production (D31).
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from cap.config import ModelBackend, get_settings
from cap.prompts import PromptSpec, new_nonce, pack_version, system_prompt

if TYPE_CHECKING:
    from anthropic.types import MessageParam, OutputConfigParam

log = logging.getLogger(__name__)


class Envelope(BaseModel):
    """Fields every prompt output must carry (Prompt Pack section 2)."""

    model_config = ConfigDict(extra="forbid")

    prompt_id: str
    prompt_version: str
    status: str = Field(pattern="^(ok|blocked)$")
    blocked_reason: str | None = None
    injection_suspected: bool = False
    explanation: str = ""


@dataclass
class ModelCall:
    spec: PromptSpec
    nonce: str
    system: str
    user: str
    output_model: type[Envelope]
    tool_results: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ModelResult[T: Envelope]:
    output: T | None
    raw: str
    stop_reason: str  # end_turn | refusal | max_tokens | error | mock
    attempts: int
    usage: dict[str, int] = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        return self.output is None or self.output.status == "blocked"


class ModelClient(ABC):
    @abstractmethod
    def _complete(self, call: ModelCall) -> tuple[str, str, dict[str, int]]:
        """Return (raw_text, stop_reason, usage)."""

    def run[T: Envelope](
        self,
        spec: PromptSpec,
        user: str,
        output_model: type[T],
        tool_results: list[dict[str, Any]] | None = None,
        nonce: str | None = None,
    ) -> ModelResult[T]:
        nonce = nonce or new_nonce()
        call = ModelCall(
            spec=spec,
            nonce=nonce,
            system=system_prompt(spec, nonce),
            user=user,
            output_model=output_model,
            tool_results=tool_results or [],
        )
        last_raw, last_stop = "", "error"
        usage: dict[str, int] = {}
        for attempt in (1, 2):  # D28: one retry then park
            raw, stop, usage = self._complete(call)
            last_raw, last_stop = raw, stop
            if stop == "refusal":
                # A refusal is not a malformed reply; do not retry, fail closed.
                return ModelResult[T](None, raw, stop, attempt, usage)
            parsed = _parse(raw, output_model, spec)
            if parsed is not None:
                return ModelResult[T](parsed, raw, stop, attempt, usage)
            log.warning(
                "prompt %s call %s: invalid output on attempt %d",
                spec.prompt_id,
                spec.call,
                attempt,
            )
        return ModelResult[T](None, last_raw, last_stop, 2, usage)


def _parse[T: Envelope](raw: str, output_model: type[T], spec: PromptSpec) -> T | None:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    try:
        out = output_model.model_validate(data)
    except ValidationError as e:
        log.debug("validation error for %s: %s", spec.prompt_id, e)
        return None
    if out.prompt_id != spec.prompt_id:
        return None
    return out


class MockClient(ModelClient):
    """Answers from fixtures: a dict keyed by (prompt_id, call) -> JSON string or a
    callable(user_text) -> dict. Missing fixture = blocked output, which is the
    fail-closed behaviour the pipeline must tolerate."""

    def __init__(self, fixtures: dict[tuple[str, str], Any] | None = None) -> None:
        self.fixtures = fixtures or {}
        self.calls: list[ModelCall] = []

    def _complete(self, call: ModelCall) -> tuple[str, str, dict[str, int]]:
        self.calls.append(call)
        fx = self.fixtures.get((call.spec.prompt_id, call.spec.call))
        if fx is None:
            fx = self.fixtures.get((call.spec.prompt_id, "*"))
        if callable(fx):
            fx = fx(call.user)
        if fx is None:
            fx = {
                "prompt_id": call.spec.prompt_id,
                "prompt_version": pack_version(),
                "status": "blocked",
                "blocked_reason": "no fixture for this prompt in mock mode",
                "injection_suspected": False,
                "explanation": "mock",
            }
        if isinstance(fx, dict):
            fx = {"prompt_id": call.spec.prompt_id, "prompt_version": pack_version(), **fx}
            fx = json.dumps(fx)
        return fx, "mock", {"input_tokens": 0, "output_tokens": 0}


class AnthropicClient(ModelClient):
    """Production backend. Reads the key from settings (Secret Manager in production).
    Uses structured outputs with the pydantic model's JSON schema and effort only."""

    def __init__(self) -> None:
        settings = get_settings()
        if settings.anthropic_api_key is None:
            raise RuntimeError(
                "CAP_ANTHROPIC_API_KEY is not set; use the mock backend or Secret Manager"
            )
        import anthropic  # imported here so the mock path never needs the SDK

        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key.get_secret_value())

    def _complete(self, call: ModelCall) -> tuple[str, str, dict[str, int]]:
        schema = call.output_model.model_json_schema()
        messages: list[dict[str, Any]] = [{"role": "user", "content": call.user}]
        if call.tool_results:
            # Sources arrive as the result of a synthetic `get_sources` tool call
            # (Prompt Pack section 2), never as plain user text.
            messages = [
                {"role": "user", "content": call.user},
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "get_sources_1",
                            "name": "get_sources",
                            "input": {},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "get_sources_1",
                            "content": json.dumps(call.tool_results),
                        }
                    ],
                },
            ]
        try:
            resp = self._client.messages.create(
                model=call.spec.model,
                max_tokens=8192,
                system=call.system,
                messages=cast("list[MessageParam]", messages),
                output_config=cast(
                    OutputConfigParam,
                    {
                        "effort": call.spec.effort,
                        "format": {"type": "json_schema", "schema": schema},
                    },
                ),
            )
        except Exception as e:  # network, 4xx, 5xx: fail closed, the caller parks the post
            log.error("anthropic call failed for %s: %s", call.spec.prompt_id, e)
            return "", "error", {}
        text = "".join(getattr(b, "text", "") for b in resp.content)
        usage = {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}
        return text, resp.stop_reason or "end_turn", usage


def get_client(fixtures: dict[tuple[str, str], Any] | None = None) -> ModelClient:
    settings = get_settings()
    if settings.model_backend == ModelBackend.ANTHROPIC:
        return AnthropicClient()
    return MockClient(fixtures)
