"""The research scout against the failure Hemant hit on 21 Oct: the search API refused
the whole request because one allow-listed domain is not reachable by its crawler."""

from types import SimpleNamespace

import anthropic
import httpx
import pytest

from cap import research

REAL_MESSAGE = (
    "Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', "
    "'message': \"The following domains are not accessible to our user agent: "
    "['newscientist.com', 'scientificamerican.com']\"}}"
)


def _bad_request(msg: str) -> anthropic.BadRequestError:
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.BadRequestError(msg, response=httpx.Response(400, request=req), body=None)


@pytest.fixture
def keyed(tmp_path, monkeypatch):
    from cap.config import get_settings

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAP_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("CAP_MODEL_BACKEND", "anthropic")
    monkeypatch.setenv("CAP_ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key")
    research._REFUSED.clear()
    get_settings.cache_clear()
    yield
    research._REFUSED.clear()
    get_settings.cache_clear()


def _fake_client(monkeypatch, behaviours):
    """behaviours: list of callables(kwargs) -> response or raise, one per call."""
    calls: list[dict] = []

    class Messages:
        def create(self, **kw):
            import copy

            calls.append(copy.deepcopy(kw))
            return behaviours[len(calls) - 1](kw)

    class Client:
        def __init__(self, *a, **kw):
            self.messages = Messages()

    monkeypatch.setattr(anthropic, "Anthropic", Client)
    return calls


def _search_reply(urls):
    result = SimpleNamespace(
        type="web_search_tool_result", content=[SimpleNamespace(url=u) for u in urls]
    )
    text = SimpleNamespace(type="text", text="\n".join(f"{u} | because" for u in urls))
    return SimpleNamespace(content=[result, text])


def _fetch_ok(monkeypatch):
    monkeypatch.setattr(
        "cap.fetch.fetch_sources",
        lambda urls, timeout=20.0: [
            {
                "url": u,
                "body": "Silk cocoons are spun by larvae.",
                "fetched_at": "",
                "page_date": "2025-05-01",
            }
            for u in urls
        ],
    )


def test_the_exact_message_is_parsed():
    assert research.refused_domains(REAL_MESSAGE) == ["newscientist.com", "scientificamerican.com"]
    assert research.refused_domains("some other 400") == []


def test_refused_domains_are_dropped_and_the_search_retried(keyed, monkeypatch):
    _fetch_ok(monkeypatch)

    def refuse(kw):
        raise _bad_request(REAL_MESSAGE)

    calls = _fake_client(
        monkeypatch,
        [refuse, lambda kw: _search_reply(["https://www.britannica.com/animal/cocoon"])],
    )
    docs, log = research.find_sources("Cocoons", "B")
    assert len(calls) == 2
    first, second = calls[0]["tools"][0]["allowed_domains"], calls[1]["tools"][0]["allowed_domains"]
    assert "newscientist.com" in first and "newscientist.com" not in second
    assert "scientificamerican.com" not in second and "britannica.com" in second
    assert [d["url"] for d in docs] == ["https://www.britannica.com/animal/cocoon"]
    assert any("left out domains" in ln and "newscientist.com" in ln for ln in log)
    # the next brief in the same run starts without the refused domains
    calls.clear()
    _fake_client(monkeypatch, [lambda kw: (calls.append(kw), _search_reply([]))[1]])
    research._search_with_model("silk", "B")
    assert "newscientist.com" not in calls[0]["tools"][0]["allowed_domains"]


def test_if_the_scout_fails_outright_wikipedia_search_takes_over(keyed, monkeypatch):
    _fetch_ok(monkeypatch)

    def boom(kw):
        raise _bad_request("Error code: 400 - something unrelated")

    _fake_client(monkeypatch, [boom])
    monkeypatch.setattr(
        research,
        "_search_wikipedia",
        lambda topic, limit=2, lang="en": ["https://en.wikipedia.org/wiki/Cocoon"],
    )
    docs, log = research.find_sources("Cocoons", "B")
    assert [d["url"] for d in docs] == ["https://en.wikipedia.org/wiki/Cocoon"]
    assert any("scout failed" in ln and "falling back" in ln for ln in log)


def test_a_cocoons_brief_reaches_the_queue(keyed, monkeypatch):
    """Hemant's brief, end to end: topic only, channel B, the API refuses two domains."""
    from cap.demo import happy_fixtures
    from cap.demo.seed import run_brief
    from cap.models import MockClient
    from cap.schema.post_record import PostStatus
    from cap.store import MemoryStore

    monkeypatch.setenv("CAP_RENDERER", "text")
    from cap.config import get_settings

    get_settings.cache_clear()
    _fetch_ok(monkeypatch)

    def refuse(kw):
        raise _bad_request(REAL_MESSAGE)

    _fake_client(
        monkeypatch,
        [refuse, lambda kw: _search_reply(["https://www.britannica.com/animal/cocoon"])],
    )
    monkeypatch.setattr("cap.models.get_client", lambda fixtures=None: MockClient(happy_fixtures()))
    rec = run_brief(MemoryStore(), "B", "Cocoons")
    assert rec.status != PostStatus.PARKED, rec.history[-1]
    assert any("source: https://www.britannica.com/animal/cocoon" in h for h in rec.history)


def test_flag_mode_unconfirmed_claims_reach_the_card_block_mode_rejects(keyed, monkeypatch):
    """D41: with nothing found to confirm the claims, flag mode queues the post with each
    claim marked unconfirmed on the card; block mode (the old guardrail) rejects it."""
    from cap.config import get_settings
    from cap.demo import happy_fixtures
    from cap.demo.seed import run_brief
    from cap.models import MockClient
    from cap.schema.post_record import PostStatus
    from cap.store import MemoryStore

    monkeypatch.setenv("CAP_RENDERER", "text")
    monkeypatch.setattr("cap.models.get_client", lambda fixtures=None: MockClient(happy_fixtures()))
    monkeypatch.setattr(
        "cap.research.find_sources",
        lambda topic, ch, claims=None: (
            [
                {
                    "url": "https://en.wikipedia.org/wiki/Cloud",
                    "body": "Clouds are water.",
                    "fetched_at": "",
                    "page_date": None,
                }
            ],
            ["research: one unrelated page"],
        ),
    )
    for mode, expect in (("flag", PostStatus.AWAITING_APPROVAL), ("block", PostStatus.REJECTED)):
        monkeypatch.setenv("CAP_FACT_MODE", mode)
        get_settings.cache_clear()
        rec = run_brief(
            MemoryStore(), "A", "Notely 7-day test", test_log="Day 3: sync failed twice."
        )
        assert rec.status == expect, (mode, rec.history[-3:])
        if mode == "flag":
            assert any("unconfirmed, flagged on the card" in h for h in rec.history)
            assert any(c.status != "supported" for c in rec.draft.claims)


def test_auto_routes_by_subject_and_patch_needs_no_test_log(keyed, monkeypatch):
    """21 Oct: 'How rainbows work' went to Patch because the form pre-selected A; and
    D42 makes Patch a general explainer, so no test log is required."""
    from cap import routing
    from cap.store import MemoryStore
    from tests.test_ui import Browser, create_app

    assert routing._heuristic("How rainbows work") == "B"
    assert routing._heuristic("How a lithium battery works") == "A"
    monkeypatch.setattr(routing, "route", lambda topic: ("B", "chosen by the model"))
    store = MemoryStore()
    api = Browser(create_app(store, run_jobs=False), follow_redirects=False)  # queued only
    api.sign_in("hemant")
    assert 'value="auto" checked' in api.get("/operator").text
    api.form("/operator/brief", channel="auto", topic="How rainbows work")
    rec = next(p for p in store.all() if p.brief.topic == "How rainbows work")
    assert rec.channel_id.value == "B" and any(
        "B · Falak (chosen by the model)" in h for h in rec.history
    )
    api.form("/operator/brief", channel="A", topic="How a lithium battery works")
    rec = next(p for p in store.all() if p.brief.topic == "How a lithium battery works")
    assert rec.status.value == "drafting" and rec.brief.format_id == "take_it_apart"
    from cap.jobs import JobKind

    queued = [j for p in store.all() for j in store.jobs.jobs_for(p.post_id)]
    assert [(j.kind, store.get(j.post_id).channel_id.value) for j in queued] == [
        (JobKind.BRIEF, "B"),
        (JobKind.BRIEF, "A"),
    ]
