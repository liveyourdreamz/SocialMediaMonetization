"""The production backend: Claude through the Anthropic SDK; the key comes from
settings (Secret Manager in production)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from cap.config import get_settings
from cap.domain.cost import usage_of

if TYPE_CHECKING:
    from anthropic.types import MessageParam, OutputConfigParam, TextBlockParam
from cap.models.base import (
    GET_SOURCES_TOOL,
    Completion,
    ModelCall,
    ModelClient,
    ModelUnreachableError,
    log,
)

SILENCE_S = 300.0  # no byte for this long on a streamed reply: the connection is gone


def _unreachable(e: Exception) -> bool:
    """Worth trying again later: the network, a timeout, rate limits, the service's own
    5xx or overload. A 4xx (bad request, bad key) would fail the same way again."""
    import anthropic
    import httpx

    transient = (
        anthropic.APIConnectionError,  # includes APITimeoutError
        anthropic.RateLimitError,
        anthropic.InternalServerError,  # 5xx, including 529 overloaded
        httpx.TransportError,  # a stream cut off mid-reply
    )
    return isinstance(e, transient)


def _failed(e: Exception, prompt_id: str) -> str:
    detail = f"{type(e).__name__}: {str(e)[:200]}"
    log.error("anthropic call failed for %s: %s", prompt_id, detail)
    if _unreachable(e):
        raise ModelUnreachableError(
            f"{prompt_id}: the model could not be reached ({detail})"
        ) from e
    return detail


class AnthropicClient(ModelClient):
    """Production backend. The key comes from settings (Secret Manager in production)."""

    def __init__(self) -> None:
        settings = get_settings()
        if settings.anthropic_api_key is None:
            raise RuntimeError(
                "CAP_ANTHROPIC_API_KEY is not set; use the mock backend or Secret Manager"
            )
        import anthropic  # imported here so the mock path never needs the SDK

        # Every call streams (`_send`). A long draft can think for minutes, but a
        # streamed reply keeps the connection alive with server events, so complete
        # silence means the connection is gone: after SILENCE_S the call fails and the
        # worker tries the step again a minute later
        # (ModelUnreachableError). Before, a non-streaming call
        # waited up to 20 minutes on a dropped Wi-Fi connection (Hemant, 25 Oct).
        self._client = anthropic.Anthropic(
            api_key=settings.anthropic_api_key.get_secret_value(),
            timeout=anthropic.Timeout(SILENCE_S, connect=10.0),
            max_retries=2,
        )

    def _send(self, **request: Any) -> Any:
        """One streamed request, accumulated into the same Message a plain call returns."""
        with self._client.messages.stream(**request) as stream:
            return stream.get_final_message()

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
            resp = self._send(
                model=req["model"],
                max_tokens=req["max_tokens"],
                system=cast("list[TextBlockParam]", req["system"]),
                messages=cast("list[MessageParam]", req["messages"]),
                output_config=cast("OutputConfigParam", req["output_config"]),
                **extra,  # type: ignore[arg-type]
            )
        except Exception as e:  # unreachable: raised (retried); 4xx: the caller parks
            return Completion(raw="", stop_reason="error", error=_failed(e, call.spec.prompt_id))
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
            usage=usage_of(resp),
            model=resp.model,
            thinking=[t for t in thinking if t],
            stop_category=getattr(details, "category", None),
        )

    # ---- the bounded tool loop (cap.models.loop) ---------------------------------------

    def build_step_request(
        self, call: ModelCall, messages: list[dict[str, Any]], tools: list[Any], final: bool
    ) -> dict[str, Any]:
        req: dict[str, Any] = {
            "model": call.spec.model,
            "max_tokens": call.max_tokens,
            "system": call.system,
            "messages": messages,
            "output_config": {
                "effort": call.spec.effort,
                "format": {"type": "json_schema", "schema": call.output_model.model_json_schema()},
            },
            "tools": [t.declaration() for t in tools],
            # the last turn has the tools switched off, so the model writes its output
            "tool_choice": {"type": "none"} if final else {"type": "auto"},
        }
        if call.log_thinking:
            req["thinking"] = {"type": "adaptive", "display": "summarized"}
        return req

    def _step(
        self, call: ModelCall, messages: list[dict[str, Any]], tools: list[Any], final: bool
    ) -> Any:
        from cap.models.loop import Step, ToolUse

        req = self.build_step_request(call, messages, tools, final)
        try:
            resp = self._send(**req)
        except Exception as e:  # unreachable: raised (retried); 4xx: the loop ends
            return Step(
                Completion(raw="", stop_reason="error", error=_failed(e, call.spec.prompt_id))
            )
        blocks = list(resp.content)
        text = "".join(getattr(b, "text", "") for b in blocks if getattr(b, "type", "") == "text")
        uses = [
            ToolUse(b.id, b.name, dict(b.input))
            for b in blocks
            if getattr(b, "type", "") == "tool_use"
        ]
        details = getattr(resp, "stop_details", None)
        return Step(
            Completion(
                raw=text,
                stop_reason=resp.stop_reason or "end_turn",
                usage=usage_of(resp),
                model=resp.model,
                stop_category=getattr(details, "category", None),
            ),
            uses,
            [b.model_dump(exclude_none=True) for b in blocks],  # thinking blocks kept as sent
        )
