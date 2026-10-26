"""What a post costs in model calls, and whether it went over its budget (D44).

Every model call notes its token usage here (`note`); a job measures the calls made
inside it (`metering`) and charges them to the post (`charge`). A post over its budget
finishes and is flagged on the card with its cost; nothing stops (D44). Hard limits
on spend sit on the API key itself, set by Hemant in the Anthropic console.

Prices are USD per million tokens, per model, from Anthropic's price page
(platform.claude.com/docs/en/about-claude/pricing), checked 24 Oct 2026 after the
second review (R9) found Haiku's cache price and long-prompt tier wrong. Each model
has its own rates, including the five-minute cache write and the cache read; Haiku
5.5 charges more when a request's prompt (input, cache reads and cache writes) is
over 100,000 tokens. A model not in the table is priced at the dearest rates, never
at zero. The Anthropic console's bill is the truth; these figures are the platform's
estimate of it. Web searches cost $10 per 1,000 on top of tokens.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from cap.schema.post_record import CostLine, PostRecord


@dataclass(frozen=True)
class Rate:
    """USD per million tokens."""

    input: float
    output: float
    cache_write: float  # five-minute cache
    cache_read: float


# model id -> tiers of (largest prompt in tokens, or None for any, rate), smallest first
PRICES: dict[str, tuple[tuple[int | None, Rate], ...]] = {
    "claude-opus-5-5": ((None, Rate(4.00, 20.00, 5.00, 0.20)),),
    "claude-sonnet-5-5": ((None, Rate(2.00, 10.00, 2.50, 0.10)),),
    "claude-haiku-5-5": (
        (100_000, Rate(0.10, 0.50, 0.125, 0.01)),
        (None, Rate(0.50, 2.50, 0.625, 0.05)),
    ),
}
_DEAREST = Rate(4.00, 20.00, 5.00, 0.20)  # an unknown model, never free
SEARCH_USD = 0.01  # one web search


def rate_for(model: str, prompt_tokens: int) -> Rate:
    """The rate for this model (an exact id, or the id with a dated suffix) and this
    prompt length."""
    tiers = next((t for mid, t in PRICES.items() if model == mid or model.startswith(mid + "-")),
                 None)  # fmt: skip
    if tiers is None:
        return _DEAREST
    return next(r for upto, r in tiers if upto is None or prompt_tokens <= upto)


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
    fresh, out = usage.get("input_tokens", 0), usage.get("output_tokens", 0)
    write, read = (
        usage.get("cache_creation_input_tokens", 0),
        usage.get("cache_read_input_tokens", 0),
    )
    r = rate_for(model, fresh + write + read)
    usd = (fresh * r.input + out * r.output + write * r.cache_write + read * r.cache_read) / 1e6
    return round(usd + usage.get("web_search_requests", 0) * SEARCH_USD, 6)


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


_CARRIED = "cap_spend"


def keep_on(error: BaseException, spent: list[Spend]) -> None:
    """A step that raised still paid for the calls it made: they travel on the error
    to whoever records the failure (cap.worker charges them to the post, review R9)."""
    setattr(error, _CARRIED, [*getattr(error, _CARRIED, []), *spent])


def carried_by(error: BaseException) -> list[Spend]:
    return list(getattr(error, _CARRIED, []))


def total(spent: list[Spend]) -> float:
    return round(sum(s.usd for s in spent), 6)


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
