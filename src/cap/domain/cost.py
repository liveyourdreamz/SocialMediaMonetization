"""What a post costs in model calls, and whether it went over its budget (D44).

Every model call notes its token usage here (`note`); a job measures the calls made
inside it (`metering`) and charges them to the post (`charge`). A post over its budget
finishes and is flagged on the card with its cost; nothing stops (D44). Hard limits
on spend sit on the API key itself, set by Hemant in the Anthropic console.

Prices are USD per million tokens, standard tier, checked 23 Oct 2026: Opus and
Sonnet agreed across two published price lists; Haiku from one. Cache writes are
priced at 1.25x input (five-minute cache), cache reads at 0.05x input. The Anthropic
console's bill is the truth; these figures are the platform's estimate of it. Web
searches are charged on top of tokens at the last published rate ($10 per 1,000).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from cap.schema.post_record import CostLine, PostRecord

# model family -> (input, output) USD per million tokens
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus": (4.00, 20.00),
    "claude-sonnet": (2.00, 10.00),
    "claude-haiku": (0.10, 0.50),
}
_UNKNOWN = PRICES["claude-opus"]  # an unknown model is priced at the dearest, never at zero
CACHE_WRITE, CACHE_READ = 1.25, 0.05  # multiples of the input price
SEARCH_USD = 0.01  # one web search


def usage_of(resp: object) -> dict[str, int]:
    """The token counts from an API reply's `usage`, as plain ints (any SDK version)."""
    u = getattr(resp, "usage", None)
    out = {
        k: int(getattr(u, k, 0) or 0)
        for k in (
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        )
    }
    tools = getattr(u, "server_tool_use", None)
    out["web_search_requests"] = int(getattr(tools, "web_search_requests", 0) or 0)
    return {k: v for k, v in out.items() if v}


@dataclass(frozen=True)
class Spend:
    what: str  # the prompt or service, e.g. "P2 call 1", "research scout"
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    usd: float


def price(model: str, usage: dict[str, int]) -> float:
    inp, out = next((p for fam, p in PRICES.items() if model.startswith(fam)), _UNKNOWN)
    usd = (
        usage.get("input_tokens", 0) * inp
        + usage.get("output_tokens", 0) * out
        + usage.get("cache_creation_input_tokens", 0) * inp * CACHE_WRITE
        + usage.get("cache_read_input_tokens", 0) * inp * CACHE_READ
    ) / 1_000_000 + usage.get("web_search_requests", 0) * SEARCH_USD
    return round(usd, 6)


_SINK: ContextVar[list[Spend] | None] = ContextVar("cap_cost_sink", default=None)


@contextmanager
def metering() -> Iterator[list[Spend]]:
    """Collect every call noted inside the block (this thread or task only)."""
    spent: list[Spend] = []
    token = _SINK.set(spent)
    try:
        yield spent
    finally:
        _SINK.reset(token)


def note(what: str, model: str, usage: dict[str, int]) -> Spend:
    """Record one call's usage with whoever is metering; returns its cost."""
    s = Spend(
        what=what,
        model=model,
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        cache_read_tokens=usage.get("cache_read_input_tokens", 0),
        cache_write_tokens=usage.get("cache_creation_input_tokens", 0),
        usd=price(model, usage),
    )
    sink = _SINK.get()
    if sink is not None:
        sink.append(s)
    return s


def charge(record: PostRecord, spent: list[Spend], budget_usd: float) -> None:
    """Add the calls to the post's cost lines and total; the first time the total
    passes the budget, say so on the record (the card shows it)."""
    if not spent:
        return
    before = record.metrics.cost_usd
    record.costs += [
        CostLine(
            what=s.what,
            model=s.model,
            input_tokens=s.input_tokens,
            output_tokens=s.output_tokens,
            cache_read_tokens=s.cache_read_tokens,
            cache_write_tokens=s.cache_write_tokens,
            usd=s.usd,
        )
        for s in spent
    ]
    record.metrics.cost_usd = round(sum(c.usd for c in record.costs), 4)
    if before <= budget_usd < record.metrics.cost_usd:
        record.log(
            f"over budget: model calls so far cost ${record.metrics.cost_usd:.2f}, above the "
            f"${budget_usd:.2f} a post (CAP_POST_BUDGET_USD); it carries on (D44)"
        )


def over_budget(record: PostRecord, budget_usd: float) -> bool:
    return record.metrics.cost_usd > budget_usd
