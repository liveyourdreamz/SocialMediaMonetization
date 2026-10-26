"""26 Oct: "Solar eclipse" (a check post) and "Pyramids" stopped after restarting.
The causes, each pinned: no placement sent to P14; checks that threw out supported
claims over typography; a signature line counted as a reused skeleton; an exhausted
API credit buried in a 400."""

from __future__ import annotations

import pytest

from cap.characters import OPENERS
from cap.gates.facts import quote_in_source
from cap.gates.originality import skeleton_share

PAGE = (
    "The Great Pyramid of Giza is the largest Egyptian pyramid. It served as the tomb "
    "of pharaoh Khufu, who ruled during the Fourth Dynasty – around 2600 BC – and was "
    "the tallest man-made structure in the world for more than 3,800 years."
)


@pytest.mark.parametrize(
    "quote",
    [
        "who ruled during the Fourth Dynasty - around 2600 BC - and was",  # dash typed
        "It served as the tomb of pharaoh Khufu ... the tallest man-made structure",
        "THE LARGEST EGYPTIAN PYRAMID",
        "tallest man‑made structure in the world for more than 3,800 years",  # nb hyphen
    ],
)
def test_a_real_quote_is_found_despite_typography(quote):
    assert quote_in_source(quote, PAGE)


@pytest.mark.parametrize(
    "quote",
    [
        "the tallest structure for 5,000 years",
        "Khufu ... built by slaves",  # one real piece, one invented
        "the ... a",  # pieces too short to prove anything
    ],
)
def test_an_invented_quote_is_still_refused(quote):
    assert not quote_in_source(quote, PAGE)


def test_the_signature_line_and_filler_openers_are_not_a_reused_skeleton():
    sig = OPENERS["B"]
    old = f"{sig} The glacier moves slowly downhill. It is one of the largest in Asia."
    new = f"{sig} The Great Pyramid stands on the Giza plateau. It is one of the oldest wonders."
    assert skeleton_share(new, [old], (sig,)) == 0.0
    copied = f"{sig} The glacier moves slowly downhill. Ice keeps flowing all year."
    assert skeleton_share(copied, [old], (sig,)) == 0.5  # a real reused opening counts


def test_an_exhausted_api_credit_is_named_and_retried_not_parked_as_a_refusal():
    import anthropic
    import httpx

    from cap.agents.outputs import P1Output
    from cap.models import ModelUnreachableError
    from cap.prompts import spec
    from tests.test_model_streaming import FakeStream, _client

    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    low = anthropic.BadRequestError(
        "Your credit balance is too low to access the Anthropic API.",
        response=httpx.Response(400, request=req), body=None,
    )  # fmt: skip
    with pytest.raises(ModelUnreachableError, match="credit is used up"):
        _client(FakeStream(error=low)).run(spec("P1", "A"), "u", P1Output)


def test_a_check_post_that_cannot_be_made_carries_on_as_a_normal_post(monkeypatch, tmp_path):
    from cap import agents
    from cap.demo.seed import new_brief
    from cap.jobs import JobKind
    from cap.schema.enums import FaultType
    from cap.schema.post_record import PostStatus
    from cap.store import MemoryStore
    from cap.worker import drain

    monkeypatch.chdir(tmp_path)
    sent: list[str] = []

    def refuse(record, client, fault, base, block="", placement=""):  # type: ignore[no-untyped-def]
        sent.append(placement)
        raise agents.AgentBlockedError("P14: cannot plant it here")

    monkeypatch.setattr(agents, "write_canary", refuse)
    store = MemoryStore()
    rec = new_brief("A", "Solar eclipse")
    rec.flags.canary, rec.flags.canary_fault_type = True, FaultType.UNSOURCED_NUMBER
    store.file(rec, JobKind.CANARY, {"urls": [], "test_log": ""})
    drain(store, rec.post_id)
    got = store.get(rec.post_id)
    assert sent == ["mid-script"]  # a placement is always sent
    assert not got.flags.canary and got.status == PostStatus.AWAITING_APPROVAL, got.history[-3:]
    assert any("carrying on as a normal post" in h for h in got.history)
    assert [j.status.value for j in store.jobs.jobs_for(rec.post_id)] == ["done"]  # no retries
