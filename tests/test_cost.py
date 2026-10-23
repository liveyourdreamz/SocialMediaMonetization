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


def test_prices_per_model_family_cache_and_search():
    assert price("claude-opus-5-5", {"input_tokens": 1_000_000}) == 4.0
    assert price("claude-sonnet-5-5", {"output_tokens": 1_000_000}) == 10.0
    assert price("claude-haiku-5-5", {"cache_read_input_tokens": 1_000_000}) == pytest.approx(0.005)
    assert price("claude-opus-5-5", {"cache_creation_input_tokens": 1_000_000}) == 5.0
    assert price("claude-sonnet-5-5", {"web_search_requests": 4}) == pytest.approx(0.04)


def test_an_unknown_model_is_priced_at_the_dearest_never_free():
    assert price("some-new-model", {"input_tokens": 1_000_000}) == 4.0


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
