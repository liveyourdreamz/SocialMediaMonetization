"""Gate 10: fact and claim check (PRD 4.14, Prompt Pack P10 v1.1.1).

Three calls, all through the model client:
* Call A (Sonnet, effort high): claim discovery over the script, with the Drafter's
  claims as a hint. Finds claims the Drafter missed and endorsement wording.
* Call B (Sonnet, effort high): per-claim verifier against the claim's source. A claim
  with no source is tried against every source the post carries.
* Call C (Haiku, effort high, own prompt, no pipeline context): second verifier on the
  same pair. Disagreement with B makes the claim unsupported; agreement adds nothing.

Message order for B and C follows the Pack: the source as a tool result first, then
the test log and the claim, then the task line (so the model quotes before it judges).

Code checks, not the model: the supporting quote must be an exact substring of the
source text; an unsupported, outdated or missing-source claim blocks the post; sources
reach the model only through the client's nonce-keyed wrapper.
"""

from __future__ import annotations

from cap.agents.outputs import P10CallAOutput, P10VerifyOutput
from cap.models import ModelClient, SourceDoc
from cap.prompts import spec
from cap.schema.post_record import Claim, ClaimCheckResult, ClaimStatus, FactsGate, PostRecord

Source = SourceDoc  # the gate's public name for a fetched document


def discover_claims(
    client: ModelClient, record: PostRecord, sources: list[SourceDoc]
) -> tuple[list[Claim], list[str], bool]:
    hints = [
        c.model_dump(include={"text", "source_url", "in_test_log"}) for c in record.draft.claims
    ]
    res = client.run(
        spec("P10", "A"),
        f"<script>{record.draft.script}</script>",
        P10CallAOutput,
        sources=sources,
        user_tail=f"<drafter_claims>{hints}</drafter_claims>",
    )
    if res.output is None or res.output.status == "blocked":
        return list(record.draft.claims), [], False
    claims = [Claim(text=c.text, source_url=c.source_url) for c in res.output.claims]
    return claims, res.output.endorsement_wording, res.output.injection_suspected


def _normalise(text: str) -> str:
    return " ".join(text.split())


def _verify(
    client: ModelClient, call: str, claim: Claim, source: SourceDoc, test_log: str
) -> tuple[ClaimStatus, str, bool, bool]:
    """Returns (status, quote, quote_in_source, injection_suspected)."""
    tail = f"<test_log>{test_log}</test_log>\n<claim>{claim.text}</claim>\nDoes the source, as written, say this?"
    res = client.run(
        spec("P10", call),
        f"<source_url>{source.url}</source_url>",
        P10VerifyOutput,
        sources=[source],
        user_tail=tail,
    )
    if res.output is None or res.output.status == "blocked":
        return "unsupported", "", False, False
    quote = res.output.supporting_quote
    in_source = bool(quote) and _normalise(quote) in _normalise(source.body)
    status: ClaimStatus = res.output.claim_status
    if status == "supported" and not in_source:
        status = "unsupported"  # a quote that is not in the source is no support
    return status, quote, in_source, res.output.injection_suspected


def check_claim(
    client: ModelClient, claim: Claim, sources: list[SourceDoc], test_log: str = ""
) -> tuple[ClaimCheckResult, bool]:
    cc = ClaimCheckResult(claim_text=claim.text, source_url=claim.source_url)
    injection = False
    candidates = [s for s in sources if claim.source_url and s.url == claim.source_url] or sources
    if not candidates:
        cc.final = "missing_source"
        cc.note = "no sources on the post"
        return cc, injection
    for src in candidates:
        cc.status_b, quote_b, cc.quote_in_source, inj_b = _verify(client, "B", claim, src, test_log)
        injection = injection or inj_b
        if cc.status_b != "supported":
            continue
        cc.status_c, _quote_c, _in_c, inj_c = _verify(client, "C", claim, src, test_log)
        injection = injection or inj_c
        if cc.status_c == "supported":
            cc.final = "supported"
            cc.source_url = src.url
            claim.source_url, claim.source_quote = src.url, quote_b
            return cc, injection
        cc.disagreement = True
        cc.note = f"second verifier disagreed on {src.url}: {cc.status_c}"
    # nothing supported it; report the most specific status seen
    cc.final = cc.status_b if cc.status_b in ("outdated", "missing_source") else "unsupported"
    return cc, injection


def check_facts(
    record: PostRecord, client: ModelClient, sources: list[SourceDoc], test_log: str = ""
) -> tuple[FactsGate, bool]:
    claims, endorsement_wording, injection = discover_claims(client, record, sources)
    gate = FactsGate(endorsement_wording=endorsement_wording)
    for claim in claims:
        cc, inj = check_claim(client, claim, sources, test_log)
        injection = injection or inj
        claim.status = cc.final
        gate.claim_checks.append(cc)
    record.draft.claims = claims
    gate.checked = len(claims)
    gate.unsupported_claims = [cc.claim_text for cc in gate.claim_checks if cc.final != "supported"]
    gate.unsupported_claims += [f"endorsement wording: {w}" for w in endorsement_wording]
    gate.passed = not gate.unsupported_claims
    return gate, injection
