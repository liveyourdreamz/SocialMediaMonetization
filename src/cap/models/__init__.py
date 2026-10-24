"""Model client: the one place that talks to Claude, with the Prompt Pack's rules baked in.

* No sampling parameters (5.5 models reject them); `output_config.effort` only.
* Structured output: the caller passes a pydantic model; the reply is validated, retried
  once (with a larger limit on a max_tokens stop), then returned as blocked (D28). A
  refusal is never retried and becomes a blocked output with reason `model_refusal`.
* Every output must carry the envelope fields; an output missing any of them is invalid.
* The client owns the untrusted-text boundary: callers pass `SourceDoc` objects, never
  pre-wrapped strings, and the client wraps each body in this call's nonce-keyed tag
  as a `get_sources` tool result (Prompt Pack section 2). Page text has no other way in.
* The system prompt is shared blocks + prompt text + closing line, followed by the
  version-locked static data blocks (rulebook, registry entry, character block) with a
  cache breakpoint on the last one (Pack section 1).
* The mock backend answers from fixtures so the pipeline runs without a key (D31).

Modules: base (the contract and shared rules), mock, claude (the Anthropic SDK).
"""

from __future__ import annotations

from cap.config import ModelBackend, get_settings
from cap.models.base import (
    GET_SOURCES_TOOL,
    MAX_OUTPUT_TOKENS,
    MAX_TOKENS,
    Completion,
    Envelope,
    FixtureValue,
    ModelCall,
    ModelClient,
    ModelResult,
    SourceDoc,
    _blocked_output,
    _envelope_block,
    _parse,
    _result,
    _system_blocks,
)
from cap.models.claude import AnthropicClient
from cap.models.mock import MockClient

__all__ = [
    "AnthropicClient",
    "Completion",
    "Envelope",
    "FixtureValue",
    "GET_SOURCES_TOOL",
    "MAX_OUTPUT_TOKENS",
    "MAX_TOKENS",
    "MockClient",
    "ModelCall",
    "ModelClient",
    "ModelResult",
    "SourceDoc",
    "_blocked_output",
    "_envelope_block",
    "_parse",
    "_result",
    "_system_blocks",
    "get_client",
]


def get_client(fixtures: dict[tuple[str, str], FixtureValue] | None = None) -> ModelClient:
    settings = get_settings()
    if settings.model_backend == ModelBackend.ANTHROPIC:
        return AnthropicClient()
    return MockClient(fixtures)
