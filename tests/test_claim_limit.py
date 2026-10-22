"""22 Oct: a Falak brief drafted 62 claims. At most settings.max_claims (CAP_MAX_CLAIMS,
default 10) per post: the drafter is told, a draft over it is rewritten once, then parks."""

import pytest

from cap.agents import AgentBlockedError, draft
from cap.config import get_settings
from cap.demo import CHARACTER_BLOCK, TEST_LOG
from cap.models import MockClient
from tests.conftest import ok, source_docs


def _claims(n: int) -> dict:
    return ok(
        claims=[
            {"text": f"fact {i}", "source_url": None, "source_quote": None, "in_test_log": False}
            for i in range(n)
        ],
        numeral_check=[],
    )


@pytest.fixture
def limit(monkeypatch):
    def set_(n: int) -> None:
        monkeypatch.setenv("CAP_MAX_CLAIMS", str(n))
        get_settings.cache_clear()

    yield set_
    get_settings.cache_clear()


def _no_numerals(fixtures):
    call1 = dict(fixtures[("P2", "1")])
    d = dict(call1["draft"])
    d["title"], d["thumbnail_text"], d["on_screen_text"] = "Silk", "Silk", []
    d["script"] = "[[HEMANT_OPENING_LINE]] A caterpillar spins silk. Then it waits."
    call1["draft"] = d
    fixtures[("P2", "1")] = call1


def test_the_default_limit_is_ten():
    get_settings.cache_clear()
    assert get_settings().max_claims == 10


def test_the_drafter_is_told_the_limit_in_both_calls(fixtures, record, limit):
    limit(7)
    _no_numerals(fixtures)
    client = MockClient(fixtures)
    draft(record, client, source_docs(), CHARACTER_BLOCK, TEST_LOG)
    p2 = [c for c in client.calls if c.spec.prompt_id == "P2"]
    assert len(p2) == 2
    for c in p2:
        text = c.user + c.user_tail
        assert "at most 7" in text, text[-300:]


def test_over_the_limit_is_rewritten_once_then_fits(fixtures, record, limit):
    limit(10)
    _no_numerals(fixtures)
    replies = [_claims(62), _claims(9)]
    fixtures[("P2", "2")] = lambda call: replies.pop(0)
    client = MockClient(fixtures)
    out = draft(record, client, source_docs(), CHARACTER_BLOCK, TEST_LOG)
    assert len(out.draft.claims) == 9
    assert any("62 claims, over the limit of 10: rewriting" in h for h in out.history)
    rewrite = [c for c in client.calls if c.spec.prompt_id == "P2" and c.spec.call == "1"][1]
    assert "the limit is 10" in str(rewrite.user)


def test_still_over_after_the_rewrite_parks_with_the_count(fixtures, record, limit):
    limit(3)
    _no_numerals(fixtures)
    fixtures[("P2", "2")] = _claims(5)
    with pytest.raises(AgentBlockedError, match=r"5 claims after a rewrite; the limit is 3"):
        draft(record, MockClient(fixtures), source_docs(), CHARACTER_BLOCK, TEST_LOG)


def test_the_limit_is_bounded(limit):
    with pytest.raises(ValueError):
        limit(0)
        get_settings()
