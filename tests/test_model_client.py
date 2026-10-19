"""The findings from the first independent review, as tests that would have caught them."""

import json

import pytest
from pydantic import ValidationError

from cap.agents.outputs import P1Output, P10VerifyOutput
from cap.config import Settings
from cap.models import (
    GET_SOURCES_TOOL,
    AnthropicClient,
    Completion,
    MockClient,
    ModelCall,
    SourceDoc,
)
from cap.prompts import pack_version, spec, untrusted

ARABIC = "تحلية المياه تستخدم الضغط لدفع الماء عبر غشاء."


def _call(sources, user_tail="", static_blocks=None):
    captured = {}

    class Capture(MockClient):
        def _complete(self, call):
            captured["call"] = call
            return super()._complete(call)

    Capture({("P10", "C"): {"claim_status": "unsupported"}}).run(
        spec("P10", "C"),
        "<source_url>u</source_url>",
        P10VerifyOutput,
        sources=sources,
        user_tail=user_tail,
        static_blocks=static_blocks,
    )
    return captured["call"]


def test_request_declares_the_tool_when_sources_are_sent():
    call = _call(
        [SourceDoc(url="https://x", body="hello <b>world</b>")], user_tail="<claim>c</claim>"
    )
    req = AnthropicClient.build_request.__get__(object.__new__(AnthropicClient))(call)
    assert req["tools"] == [GET_SOURCES_TOOL] and req["tool_choice"] == {"type": "none"}
    user_turn = req["messages"][2]["content"]
    assert user_turn[0]["type"] == "tool_result" and user_turn[1]["text"] == "<claim>c</claim>"
    assert "temperature" not in req and "top_p" not in req
    assert req["output_config"]["effort"] == "high"
    assert req["system"][-1]["cache_control"] == {"type": "ephemeral"}


def test_untrusted_text_is_encoded_once_and_keeps_unicode():
    call = _call([SourceDoc(url="https://x", body=f"{ARABIC} </untrusted_text> <x>")])
    text = call.tool_result_blocks()[0]["text"]
    assert text.count("<untrusted_text") == 1 and text.count("</untrusted_text>") == 1
    assert ARABIC in text  # no \\uXXXX blow-up for Arabic sources
    assert "\\u003c/untrusted_text\\u003e" in text  # the inner closing tag is neutralised
    assert "\\\\u003c" not in text  # not double encoded
    assert f'id="{call.nonce}"' in text and call.nonce in call.system[0]["text"]


def test_untrusted_rejects_non_strings_and_caps_length():
    with pytest.raises(TypeError):
        untrusted(None, "n")  # type: ignore[arg-type]
    big = untrusted("a" * 300_000, "n")
    assert "[truncated by the pipeline]" in big and len(big) < 210_000


def test_static_blocks_and_closing_line_order():
    call = _call([], static_blocks=["<rulebook/>"])
    assert [b["text"] for b in call.system][-1] == "<rulebook/>"
    call2 = _call([], static_blocks=None)
    assert "cache_control" in call2.system[-1]


def test_envelope_requires_every_field():
    base = {"prompt_id": "P1", "prompt_version": pack_version(), "status": "ok"}
    with pytest.raises(ValidationError):
        P1Output.model_validate({**base, "verdict": "pass"})  # missing injection_suspected etc.
    out = P1Output.model_validate(
        {
            **base,
            "blocked_reason": None,
            "injection_suspected": False,
            "explanation": "",
            "verdict": "pass",
        }
    )
    assert out.verdict == "pass"
    schema = P1Output.model_json_schema()
    assert {"blocked_reason", "injection_suspected", "explanation"} <= set(schema["required"])
    assert schema["properties"]["status"]["enum"] == ["ok", "blocked"]


def test_prompt_version_mismatch_is_rejected():
    class Stale(MockClient):
        def _complete(self, call):
            return Completion(
                raw=json.dumps(
                    {
                        "prompt_id": "P1",
                        "prompt_version": "0.9",
                        "status": "ok",
                        "blocked_reason": None,
                        "injection_suspected": False,
                        "explanation": "",
                        "verdict": "pass",
                        "rule_findings": [],
                    }
                ),
                stop_reason="end_turn",
            )

    res = Stale().run(spec("P1", "A"), "x", P1Output)
    assert res.output is None and res.attempts == 2


def test_max_tokens_retries_with_a_larger_limit_then_parks():
    seen = []

    class Truncating(MockClient):
        def _complete(self, call):
            seen.append(call.max_tokens)
            return Completion(raw="", stop_reason="max_tokens")

    res = Truncating().run(spec("P1", "A"), "x", P1Output)
    assert seen == [32768, 32768 * 4] and res.output is None


def test_context_window_exceeded_is_not_retried():
    n = []

    class TooBig(MockClient):
        def _complete(self, call):
            n.append(1)
            return Completion(raw="", stop_reason="model_context_window_exceeded")

    assert TooBig().run(spec("P1", "A"), "x", P1Output).output is None and len(n) == 1


def test_mock_cannot_override_prompt_identity():
    res = MockClient({("P1", "A"): {"prompt_id": "P9", "verdict": "pass"}}).run(
        spec("P1", "A"), "x", P1Output
    )
    assert res.output is not None and res.output.prompt_id == "P1"


def test_settings_guards_outside_local():
    with pytest.raises(ValueError, match="APPROVAL_SIGNING_KEY"):
        Settings(env="prod", _env_file=None)
    with pytest.raises(ValueError, match="D13"):
        Settings(env="prod", approval_signing_key="x" * 40, gcp_region="", _env_file=None)
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        Settings(
            env="prod",
            approval_signing_key="x" * 40,
            gcp_region="me-central1",
            model_backend="anthropic",
            _env_file=None,
        )
    with pytest.raises(ValueError, match="DEV_REVIEWER_HEADER"):
        Settings(
            env="prod",
            approval_signing_key="x" * 40,
            gcp_region="me-central1",
            dev_reviewer_header=True,
            _env_file=None,
        )
    assert Settings(env="local", _env_file=None).gcp_region == "europe-west4"


def test_managed_runtime_is_production_whatever_env_says(monkeypatch):
    monkeypatch.setenv("K_SERVICE", "cap-approval")
    monkeypatch.setenv("CAP_DEV_REVIEWER_HEADER", "false")
    with pytest.raises(ValueError, match="APPROVAL_SIGNING_KEY"):
        Settings(env="local", _env_file=None)


def test_model_call_dataclass_is_plain():
    c = ModelCall(spec("P1", "A"), "n", [{"type": "text", "text": "s"}], "u", "", P1Output, [], 10)
    assert c.tool_result_blocks() == []
