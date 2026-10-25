"""Every real model call streams (25 Oct: a dropped Wi-Fi connection left a draft
waiting 20 minutes): silence on a stream means the connection is gone, so the call fails
after SILENCE_S and the worker's retry takes over."""

from __future__ import annotations

from types import SimpleNamespace

from cap.agents.outputs import P1Output
from cap.models import AnthropicClient
from cap.models.claude import SILENCE_S
from cap.prompts import spec


class FakeStream:
    def __init__(self, final=None, error=None):  # type: ignore[no-untyped-def]
        self.final, self.error, self.kwargs = final, error, None

    def __call__(self, **kwargs):  # type: ignore[no-untyped-def]
        self.kwargs = kwargs
        return self

    def __enter__(self):  # type: ignore[no-untyped-def]
        if self.error:
            raise self.error
        return self

    def __exit__(self, *a):  # type: ignore[no-untyped-def]
        return False

    def get_final_message(self):  # type: ignore[no-untyped-def]
        return self.final


def _client(stream):  # type: ignore[no-untyped-def]
    c = object.__new__(AnthropicClient)
    c._client = SimpleNamespace(messages=SimpleNamespace(stream=stream))
    return c


REPLY = SimpleNamespace(
    content=[SimpleNamespace(type="text", text="{}")], stop_reason="end_turn",
    model="claude-opus-5-5", usage=SimpleNamespace(input_tokens=5, output_tokens=7),
)  # fmt: skip


def test_calls_stream_and_read_the_final_message():
    s = FakeStream(final=REPLY)
    out = _client(s).run(spec("P1", "A"), "u", P1Output)
    assert s.kwargs["model"] == spec("P1", "A").model and "output_config" in s.kwargs
    assert out.usage == {"input_tokens": 5, "output_tokens": 7}


def test_a_dead_connection_raises_so_the_worker_tries_again():
    """Not a refusal (that parks the post): the step fails and is retried later."""
    import httpx
    import pytest

    from cap.models import ModelUnreachableError

    s = FakeStream(error=httpx.ReadTimeout("no data for 300 s"))
    with pytest.raises(ModelUnreachableError, match="ReadTimeout"):
        _client(s).run(spec("P1", "A"), "u", P1Output)


def test_the_sdk_connection_error_raises_too_but_a_bad_request_does_not():
    import anthropic
    import httpx
    import pytest

    from cap.models import ModelUnreachableError

    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    with pytest.raises(ModelUnreachableError):
        _client(FakeStream(error=anthropic.APIConnectionError(request=req))).run(
            spec("P1", "A"), "u", P1Output
        )
    bad = anthropic.BadRequestError("bad", response=httpx.Response(400, request=req), body=None)
    out = _client(FakeStream(error=bad)).run(spec("P1", "A"), "u", P1Output)
    assert out.output is None and "BadRequestError" in (out.reason or "")


def test_silence_is_minutes_not_twenty():
    assert 60 <= SILENCE_S <= 300
