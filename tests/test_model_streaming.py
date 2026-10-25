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


def test_a_dead_connection_is_an_error_result_not_a_hang():
    import httpx

    s = FakeStream(error=httpx.ReadTimeout("no data for 300 s"))
    out = _client(s).run(spec("P1", "A"), "u", P1Output)
    assert out.output is None and "ReadTimeout" in (out.reason or "")


def test_silence_is_minutes_not_twenty():
    assert 60 <= SILENCE_S <= 300
