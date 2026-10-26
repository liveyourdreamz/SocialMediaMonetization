"""D44: every model call is metered, each post carries its cost, and a post over its
budget is flagged on the card, never stopped."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from cap.config import get_settings
from cap.domain.cost import charge, metering, note, over_budget, price, usage_of
from cap.models import MockClient
from cap.pipeline import run_post
from cap.schema.post_record import PostStatus
from cap.ui.views import cost_view
from tests.conftest import make_deps
from tests.test_gates_and_pipeline import SOURCES

BILLED = {"input_tokens": 10_000, "output_tokens": 2_000}


class BilledClient(MockClient):
    """The mock's answers, with token usage the way the real API reports it."""

    def _complete(self, call):  # type: ignore[no-untyped-def]
        c = super()._complete(call)
        c.usage, c.model = dict(BILLED), "claude-sonnet-5-5"
        return c


def test_prices_per_model_cache_and_search():
    """Anthropic's price page, 24 Oct 2026 (review R9)."""
    assert price("claude-opus-5-5", {"input_tokens": 1_000_000}) == 4.0
    assert price("claude-sonnet-5-5", {"output_tokens": 1_000_000}) == 10.0
    assert price("claude-opus-5-5", {"cache_creation_input_tokens": 1_000_000}) == 5.0
    assert price("claude-sonnet-5-5", {"cache_read_input_tokens": 1_000_000}) == pytest.approx(0.10)
    assert price("claude-sonnet-5-5", {"web_search_requests": 4}) == pytest.approx(0.04)
    assert price("claude-sonnet-5-5-20260601", {"input_tokens": 1_000_000}) == 2.0  # dated id


def test_haiku_cache_reads_are_a_tenth_and_long_prompts_cost_more():
    assert price("claude-haiku-5-5", {"cache_read_input_tokens": 100_000}) == pytest.approx(0.001)
    short = {"input_tokens": 60_000, "cache_read_input_tokens": 40_000, "output_tokens": 1_000}
    assert price("claude-haiku-5-5", short) == pytest.approx((6_000 + 400 + 500) / 1e6)
    long = {**short, "input_tokens": 60_001}  # 100,001 prompt tokens: the higher tier
    assert price("claude-haiku-5-5", long) == pytest.approx(
        (60_001 * 0.50 + 40_000 * 0.05 + 1_000 * 2.50) / 1e6,
        abs=1e-6,  # rounded to 6 places
    )


def test_an_unknown_model_is_priced_at_the_dearest_never_free():
    assert price("some-new-model", {"input_tokens": 1_000_000}) == 4.0
    assert price("claude-opus-5", {"input_tokens": 1_000_000}) == 4.0  # not another model's


def test_usage_is_read_from_any_sdk_reply_shape():
    resp = SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=5,
            output_tokens=7,
            cache_read_input_tokens=None,  # older replies leave these None
            server_tool_use=SimpleNamespace(web_search_requests=2),
        )
    )
    assert usage_of(resp) == {"input_tokens": 5, "output_tokens": 7, "web_search_requests": 2}
    assert usage_of(SimpleNamespace()) == {}


def test_metering_collects_only_inside_the_block():
    note("outside", "claude-haiku-5-5", BILLED)  # nobody metering: dropped, no error
    with metering() as spent:
        note("inside", "claude-haiku-5-5", BILLED)
    assert [s.what for s in spent] == ["inside"]


def test_the_mock_backend_charges_nothing(client, record, tmp_path):
    out = run_post(record, SOURCES, make_deps(client, record, tmp_path))
    assert out.costs == [] and out.metrics.cost_usd == 0.0
    assert "no model calls charged" in cost_view(out)["line"]


def test_every_billed_call_is_charged_to_the_post(fixtures, record, tmp_path):
    out = run_post(record, SOURCES, make_deps(BilledClient(fixtures), record, tmp_path))
    assert out.status == PostStatus.AWAITING_APPROVAL
    assert len(out.costs) >= 5  # drafter, critic, adapter, the gates' judges
    each = price("claude-sonnet-5-5", BILLED)
    assert out.metrics.cost_usd == pytest.approx(each * len(out.costs), abs=1e-4)
    assert {c.what.split()[0] for c in out.costs} >= {"P2"}


def test_over_budget_is_flagged_once_and_never_stops_the_post(
    fixtures, record, tmp_path, monkeypatch
):
    monkeypatch.setenv("CAP_POST_BUDGET_USD", "0.05")
    get_settings.cache_clear()
    out = run_post(record, SOURCES, make_deps(BilledClient(fixtures), record, tmp_path))
    assert out.status == PostStatus.AWAITING_APPROVAL  # D44: it carries on
    assert over_budget(out, 0.05)
    assert sum("over budget" in line for line in out.history) == 1
    view = cost_view(out)
    assert view["over"] and "over budget" in view["line"]


def test_charging_adds_to_what_the_post_already_cost(record):
    with metering() as spent:
        note("P2 call 1", "claude-opus-5-5", {"output_tokens": 100_000})  # $2.00
    charge(record, spent, 3.0)
    charge(record, spent, 3.0)  # a second step: $4.00 in all, over $3.00
    assert record.metrics.cost_usd == pytest.approx(4.0)
    assert len(record.costs) == 2
    assert sum("over budget" in line for line in record.history) == 1


# ---- review R9: nothing paid for goes unrecorded ---------------------------------------


def test_a_step_that_raises_keeps_its_spend_on_the_error(record):
    from cap.domain.cost import carried_by
    from cap.pipeline import _metered

    @_metered
    def step(rec):  # type: ignore[no-untyped-def]
        note("P2 call 1", "claude-sonnet-5-5", BILLED)
        raise RuntimeError("the voice service fell over")

    with pytest.raises(RuntimeError) as err:
        step(record)
    assert [s.what for s in carried_by(err.value)] == ["P2 call 1"]


def _worker_with(kind, handler):  # type: ignore[no-untyped-def]
    from cap.demo.seed import new_brief
    from cap.jobs import JobKind
    from cap.store import MemoryStore
    from cap.worker import Worker

    store = MemoryStore()
    rec = new_brief("A", "How tides work")
    store.file(rec, kind, {})
    w = Worker(store, {k: handler for k in JobKind})
    assert w.run_once()
    return store, rec.post_id


def test_the_worker_charges_calls_made_outside_any_step():
    from cap.jobs import JobKind

    def canary_writer(store, job):  # type: ignore[no-untyped-def]
        note("P14", "claude-sonnet-5-5", BILLED)

    store, pid = _worker_with(JobKind.CANARY, canary_writer)
    got = store.get(pid)
    assert [c.what for c in got.costs] == ["P14"]
    assert got.metrics.cost_usd == pytest.approx(price("claude-sonnet-5-5", BILLED))


def test_a_failed_job_still_charges_what_it_paid_for():
    from cap.jobs import JobKind
    from cap.pipeline import _metered

    @_metered
    def step(rec):  # type: ignore[no-untyped-def]
        note("P2 call 1", "claude-sonnet-5-5", BILLED)
        raise RuntimeError("down")

    def handler(store, job):  # type: ignore[no-untyped-def]
        note("routing", "claude-haiku-5-5", BILLED)  # outside the step
        step(store.get(job.post_id))

    store, pid = _worker_with(JobKind.BRIEF, handler)
    got = store.get(pid)
    assert got.status == PostStatus.PARKED
    assert sorted(c.what for c in got.costs) == ["P2 call 1", "routing"]


def test_a_trend_research_runs_cost_is_in_the_audit_log():
    from cap.jobs import JobKind

    def research(store, job):  # type: ignore[no-untyped-def]
        for _ in range(3):
            note("P4 turn", "claude-haiku-5-5", BILLED)

    store, _ = _worker_with(JobKind.IDEAS, research)
    (entry,) = [e for e in store.audit.entries() if e.action == "ideas run cost"]
    assert entry.detail["calls"] == 3
    assert entry.detail["usd"] == pytest.approx(3 * price("claude-haiku-5-5", BILLED))
