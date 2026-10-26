"""22 Oct: P7 stopped a real post because length_s, the sponsor status and the
platform limits were never sent. Every call that reads the post now carries the
post_context block with every value the prompts expect, filled in by code."""

import pytest

from cap.config import get_settings

NEEDED = (
    "length_s: 30",
    "sponsored: no",
    "affiliate links: none",
    "claim limit: at most 10",
    "exemplars: none yet",
    "youtube_short: title at most 100 characters; duration at most 60 seconds",
    "do not report any of them as missing",
)


@pytest.fixture
def calls(tmp_path, monkeypatch):
    import cap.models
    from cap.demo import happy_fixtures
    from cap.demo.seed import run_brief
    from cap.models import MockClient
    from cap.store import MemoryStore

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAP_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("CAP_RENDERER", "text")
    monkeypatch.setenv("CAP_MODEL_BACKEND", "anthropic")
    monkeypatch.setenv("CAP_ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key")
    get_settings.cache_clear()
    made: list[MockClient] = []

    def client(fixtures=None):
        made.append(MockClient(happy_fixtures()))
        return made[-1]

    monkeypatch.setattr(cap.models, "get_client", client)
    monkeypatch.setattr(
        "cap.research.find_sources", lambda t, c, claims=None: ([], ["research: none"])
    )
    run_brief(MemoryStore(), "A", "How a rainbow works")
    yield [c for m in made for c in m.calls]
    get_settings.cache_clear()


WRITERS = {"P2", "P3", "P7"}
CHECKERS = {"P1", "P9"} | {"P10"}
COMMON = (
    "length_s: 30",
    "youtube_short: title at most 100 characters; duration at most 60 seconds",
    "[[HEMANT_OPENING_LINE]] marks where",
)


def test_writers_get_every_value_as_given(calls):
    seen = set()
    for c in calls:
        if c.spec.prompt_id in WRITERS:
            seen.add(c.spec.prompt_id)
            text = c.user + c.user_tail
            for v in NEEDED + COMMON:
                assert v in text, (c.spec.prompt_id, c.spec.call, v)
    assert seen == WRITERS, seen


def test_checkers_get_what_the_brief_recorded_never_a_relaxed_rule(calls):
    """Regression run 22 Oct: told "a claim without a source is expected", a judge
    could pass an unsourced health claim (R-09); told "sponsored: no", it escalated
    real sponsored posts it could see. Checkers get the record, not instructions."""
    seen = set()
    for c in calls:
        pid = c.spec.prompt_id
        if pid not in CHECKERS or (pid == "P10" and c.spec.call != "A"):
            continue
        seen.add(pid)
        text = c.user + c.user_tail
        for v in COMMON:
            assert v in text, (pid, v)
        assert "never overrides what the content shows" in text
        assert "relaxes no rule that requires a visible source" in text
        assert "not a defect" not in text and "affiliate links: none" not in text
        assert "do not report any of them as missing" not in text
    assert seen == CHECKERS, seen


def test_the_verifiers_stay_blind(calls):
    for c in calls:
        if c.spec.prompt_id == "P10" and c.spec.call in ("B", "C"):
            assert "<post_context>" not in c.user + c.user_tail


def test_a_sponsored_post_says_so():
    from cap.post_context import post_context
    from cap.schema.post_record import Channel, PostRecord, Sponsor

    rec = PostRecord(channel_id=Channel.A)
    rec.sponsor = Sponsor.model_validate(
        {"sponsor_id": "s1", "name": "Acme", "permit_status": "granted"}
    )
    assert "sponsored: yes" in post_context(rec)
