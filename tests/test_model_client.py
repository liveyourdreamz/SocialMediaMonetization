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


def _envelope(**extra):
    base = {
        "prompt_id": "P1",
        "prompt_version": pack_version(),
        "status": "ok",
        "blocked_reason": None,
        "injection_suspected": False,
        "explanation": "",
        "verdict": "pass",
        "rule_findings": [],
    }
    base.update(extra)
    return base


def _client_returning(raw: str, stop_reason: str = "end_turn", error: str = ""):
    class C(MockClient):
        def _complete(self, call):
            return Completion(raw=raw, stop_reason=stop_reason, error=error)

    return C()


def test_the_model_is_told_the_envelope_values_it_must_echo():
    """The first real-model run parked every post: nothing told the model the pack
    version, so it guessed and the parser rejected it. The system prompt now states
    prompt_id and prompt_version, and the pipeline stamps them regardless."""
    seen = {}

    class Spy(MockClient):
        def _complete(self, call):
            seen["system"] = "".join(b["text"] for b in call.system)
            return Completion(raw=json.dumps(_envelope()), stop_reason="end_turn")

    Spy().run(spec("P1", "A"), "x", P1Output)
    assert f'"prompt_version" to "{pack_version()}"' in seen["system"]
    assert '"prompt_id" to "P1"' in seen["system"]


def test_a_wrong_envelope_version_is_stamped_not_rejected():
    res = _client_returning(json.dumps(_envelope(prompt_version="0.9", prompt_id="P9"))).run(
        spec("P1", "A"), "x", P1Output
    )
    assert res.output is not None and res.attempts == 1
    assert res.output.prompt_version == pack_version() and res.output.prompt_id == "P1"


def test_an_unusable_output_names_the_reason_for_the_park():
    res = _client_returning(json.dumps(_envelope(verdict="maybe"))).run(
        spec("P1", "A"), "x", P1Output
    )
    assert res.output is None and res.reason.startswith("schema: verdict:")
    res = _client_returning("Sure! Here is the JSON you asked for: {").run(
        spec("P1", "A"), "x", P1Output
    )
    assert res.output is None and res.reason.startswith("not JSON")
    res = _client_returning("", "error", "AuthenticationError: invalid x-api-key").run(
        spec("P1", "A"), "x", P1Output
    )
    assert res.output is None and "AuthenticationError" in res.reason


def test_max_tokens_retries_with_a_larger_limit_then_parks():
    seen = []

    class Truncating(MockClient):
        def _complete(self, call):
            seen.append(call.max_tokens)
            return Completion(raw="", stop_reason="max_tokens")

    res = Truncating().run(spec("P1", "A"), "x", P1Output)
    assert seen == [32768, 128_000] and res.output is None  # capped at the model limit


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


def test_the_production_client_sets_an_explicit_timeout(monkeypatch):
    """With the SDK's default timeout, any non-streaming call above ~21k max_tokens
    raises 'Streaming is required' before a request is sent: every high-effort prompt
    parked on the first laptop run. The client must set its own timeout."""
    import anthropic

    from cap.config import get_settings
    from cap.models import MAX_TOKENS, AnthropicClient

    monkeypatch.setenv("CAP_MODEL_BACKEND", "anthropic")
    monkeypatch.setenv("CAP_ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key")
    get_settings.cache_clear()
    try:
        c = AnthropicClient()._client
        assert c.timeout != anthropic.DEFAULT_TIMEOUT
        for n in MAX_TOKENS.values():  # the guard only runs with the default timeout
            c._calculate_nonstreaming_timeout(
                n, None
            ) if c.timeout == anthropic.DEFAULT_TIMEOUT else None
    finally:
        get_settings.cache_clear()
