"""Gate 10: fact and claim check (PRD 4.14, Prompt Pack P10 v1.1.1).

Three calls, all through the model client:
* Call A (Sonnet, effort high): claim discovery over the script, with the Drafter's
  claims as a hint. Finds claims the Drafter missed and endorsement wording.
* Call B (Sonnet, effort high): per-claim verifier against the claim's source. A claim
  with no source is tried against every source the post carries.
* Call C (Haiku, effort high, own prompt, no pipeline context): second verifier on the
  same pair. Disagreement with B makes the claim unsupported; agreement adds nothing.

Code checks, not the model: the supporting quote must be an exact substring of the
source text; an unsupported, outdated or missing-source claim blocks the post; sources
reach the model only as JSON tool results inside nonce-keyed tags (section 2).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cap.agents.outputs import P10CallAOutput, P10VerifyOutput
from cap.models import ModelClient
from cap.prompts import new_nonce, spec, untrusted
from cap.schema.post_record import Claim, FactsGate, PostRecord


@dataclass
class Source:
    url: str
    body: str
    fetched_at: str = ""
    page_date: str | None = None
    fast_moving_topic: bool = False


@dataclass
class ClaimCheck:
    claim: Claim
    status_b: str = "unsupported"
    status_c: str = "unsupported"
    quote_b: str = ""
    quote_c: str = ""
    final: str = "unsupported"
    notes: list[str] = field(default_factory=list)
    injection_suspected: bool = False


def _tool_results(sources: list[Source], nonce: str) -> list[dict]:
    """One JSON object per source, body wrapped in the nonce-keyed tag (section 2)."""
    return [
        {
            "source_url": s.url,
            "fetched_at": s.fetched_at,
            "page_date": s.page_date,
            "body": untrusted(s.body, nonce),
        }
        for s in sources
    ]


def discover_claims(
    client: ModelClient, record: PostRecord, sources: list[Source]
) -> tuple[list[Claim], list[str], bool]:
    nonce = new_nonce()
    user = (
        f"<script>{record.draft.script}</script>\n"
        f"<drafter_claims>{[c.model_dump(include={'text', 'source_url'}) for c in record.draft.claims]}</drafter_claims>"
    )
    res = client.run(
        spec("P10", "A"),
        user,
        P10CallAOutput,
        tool_results=_tool_results(sources, nonce),
        nonce=nonce,
    )
    if res.output is None or res.output.status == "blocked":
        return list(record.draft.claims), [], True
    claims = [Claim(text=c.text, source_url=c.source_url) for c in res.output.claims]
    return claims, res.output.endorsement_wording, res.output.injection_suspected


def _verify(
    client: ModelClient, call: str, claim: Claim, source: Source, nonce: str
) -> tuple[str, str, bool]:
    user = f"<claim>{claim.text}</claim>\n<source_url>{source.url}</source_url>"
    if source.fast_moving_topic:
        user += "\n<topic_speed>fast_moving</topic_speed>"
    res = client.run(
        spec("P10", call),
        user,
        P10VerifyOutput,
        tool_results=_tool_results([source], nonce),
        nonce=nonce,
    )
    if res.output is None or res.output.status == "blocked":
        return "unsupported", "", False
    quote = res.output.supporting_quote
    # exact-substring check in code: a quote that is not in the source is no support
    if res.output.claim_status == "supported" and (not quote or quote not in source.body):
        return "unsupported", quote, res.output.injection_suspected
    return res.output.claim_status, quote, res.output.injection_suspected


def check_claim(client: ModelClient, claim: Claim, sources: list[Source]) -> ClaimCheck:
    cc = ClaimCheck(claim=claim)
    candidates = [s for s in sources if claim.source_url and s.url == claim.source_url] or sources
    if not candidates:
        cc.final = "missing_source"
        cc.notes.append("no sources on the post")
        return cc
    nonce = new_nonce()
    for src in candidates:
        cc.status_b, cc.quote_b, inj_b = _verify(client, "B", claim, src, nonce)
        cc.injection_suspected = cc.injection_suspected or inj_b
        if cc.status_b != "supported":
            continue
        cc.status_c, cc.quote_c, inj_c = _verify(client, "C", claim, src, nonce)
        cc.injection_suspected = cc.injection_suspected or inj_c
        if cc.status_c == "supported":
            cc.final = "supported"
            cc.claim.source_url = src.url
            cc.claim.source_quote = cc.quote_b
            return cc
        cc.notes.append(f"second verifier disagreed on {src.url}: {cc.status_c}")
    # nothing supported it; report the most specific status seen
    cc.final = cc.status_b if cc.status_b in ("outdated", "missing_source") else "unsupported"
    return cc


def check_facts(
    record: PostRecord, client: ModelClient, sources: list[Source]
) -> tuple[FactsGate, list[ClaimCheck], bool]:
    claims, endorsement_wording, injection = discover_claims(client, record, sources)
    checks = [check_claim(client, c, sources) for c in claims]
    for cc in checks:
        cc.claim.status = cc.final  # type: ignore[assignment]
        injection = injection or cc.injection_suspected
    record.draft.claims = [cc.claim for cc in checks]
    unsupported = [cc.claim.text for cc in checks if cc.final != "supported"]
    unsupported += [f"endorsement wording: {w}" for w in endorsement_wording]
    gate = FactsGate(unsupported_claims=unsupported, checked=len(checks), passed=not unsupported)
    return gate, checks, injection
