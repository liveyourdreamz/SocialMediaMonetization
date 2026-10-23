"""The production backend: Claude through the Anthropic SDK; the key comes from
settings (Secret Manager in production)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from cap.config import get_settings

if TYPE_CHECKING:
    from anthropic.types import MessageParam, OutputConfigParam, TextBlockParam
from cap.models.base import (
    GET_SOURCES_TOOL,
    Completion,
    ModelCall,
    ModelClient,
    log,
)


class AnthropicClient(ModelClient):
    """Production backend. The key comes from settings (Secret Manager in production)."""

    def __init__(self) -> None:
        settings = get_settings()
        if settings.anthropic_api_key is None:
            raise RuntimeError(
                "CAP_ANTHROPIC_API_KEY is not set; use the mock backend or Secret Manager"
            )
        import anthropic  # imported here so the mock path never needs the SDK

        # An explicit timeout: with the SDK default, any non-streaming call whose
        # max_tokens exceeds ~21k raises before the request is sent ("Streaming is
        # required..."), which parked every high-effort prompt on the first laptop run.
        self._client = anthropic.Anthropic(
            api_key=settings.anthropic_api_key.get_secret_value(),
            timeout=anthropic.Timeout(1200.0, connect=10.0),
            max_retries=2,
        )

    def build_request(self, call: ModelCall) -> dict[str, Any]:
        """The exact request body; separated from the send so tests can check it."""
        schema = call.output_model.model_json_schema()
        messages: list[dict[str, Any]] = [{"role": "user", "content": call.user}]
        kwargs: dict[str, Any] = {}
        if call.sources:
            # Sources arrive as the result of a synthetic `get_sources` tool call, with
            # the tool declared (the API rejects tool blocks otherwise) and tool_choice
            # none so the model cannot issue a second call.
            tail: list[dict[str, Any]] = [
                {
                    "type": "tool_result",
                    "tool_use_id": "get_sources_1",
                    "content": call.tool_result_blocks(),
                }
            ]
            if call.user_tail:
                tail.append({"type": "text", "text": call.user_tail})
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
                {"role": "user", "content": tail},
            ]
            kwargs["tools"] = [GET_SOURCES_TOOL]
            kwargs["tool_choice"] = {"type": "none"}
        elif call.user_tail:
            messages = [{"role": "user", "content": f"{call.user}\n\n{call.user_tail}"}]
        if call.log_thinking:
            kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}
        return {
            "model": call.spec.model,
            "max_tokens": call.max_tokens,
            "system": call.system,
            "messages": messages,
            "output_config": {
                "effort": call.spec.effort,
                "format": {"type": "json_schema", "schema": schema},
            },
            **kwargs,
        }

    def _complete(self, call: ModelCall) -> Completion:
        req = self.build_request(call)
        try:
            extra: dict[str, Any] = {
                k: v for k, v in req.items() if k in ("tools", "tool_choice", "thinking")
            }
            resp = self._client.messages.create(
                model=req["model"],
                max_tokens=req["max_tokens"],
                system=cast("list[TextBlockParam]", req["system"]),
                messages=cast("list[MessageParam]", req["messages"]),
                output_config=cast("OutputConfigParam", req["output_config"]),
                **extra,  # type: ignore[arg-type]
            )
        except Exception as e:  # network, 4xx, 5xx: fail closed, the caller parks the post
            detail = f"{type(e).__name__}: {str(e)[:200]}"
            log.error("anthropic call failed for %s: %s", call.spec.prompt_id, detail)
            return Completion(raw="", stop_reason="error", error=detail)
        text = "".join(
            getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text"
        )
        thinking = [
            getattr(b, "thinking", "") for b in resp.content if getattr(b, "type", "") == "thinking"
        ]
        details = getattr(resp, "stop_details", None)
        return Completion(
            raw=text,
            stop_reason=resp.stop_reason or "end_turn",
            usage={
                "input_tokens": resp.usage.input_tokens,
                "output_tokens": resp.usage.output_tokens,
            },
            model=resp.model,
            thinking=[t for t in thinking if t],
            stop_category=getattr(details, "category", None),
        )
