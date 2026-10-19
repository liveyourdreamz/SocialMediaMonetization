"""Gate 10: fact and claim check (PRD 4.14, Prompt Pack P10 v1.1.1).

Three calls, all through the model client:
* Call A (Sonnet, effort high): claim discovery over the script and the variant titles
  and captions, with the Drafter's claims as a hint. Claims it adds are checked too.
* Call B (Sonnet, effort high): per-claim verifier against the claim's source. A claim
  with no source is tried against every source the post carries; a claim citing a URL
  the post does not carry is missing_source.
* Call C (Haiku, effort high, own prompt, no pipeline context): second verifier on
  every claim B supports. Disagreement makes the claim unsupported.

The pipeline's own test log is a source too (url testlog://<post>), so a claim the
Drafter marked in_test_log is verified against it like any other.

Message order for B and C follows the Pack: the source as a tool result first, then
the test log and the claim, then the task line.

Code checks, not the model: the supporting quote must be an exact substring of the
source (whitespace-normalised, unicode-normalised, and also tested after decoding the
JSON escapes the model saw); an unsupported, outdated or missing-source claim blocks
the post; a blocked discovery call parks the post; sources reach the model only
through the client's nonce-keyed wrapper.
"""

from __future__ import annotations

import json
import unicodedata

from cap.agents import AgentBlockedError
from cap.agents.outputs import P10CallAOutput, P10VerifyOutput
from cap.models import ModelClient, SourceDoc
from cap.prompts import spec
from cap.schema.post_record import Claim, ClaimCheckResult, ClaimStatus, FactsGate, PostRecord

Source = SourceDoc  # the gate's public name for a fetched document
TESTLOG_SCHEME = "testlog://"


def testlog_source(record: PostRecord, test_log: str) -> SourceDoc:
    return SourceDoc(url=f"{TESTLOG_SCHEME}{record.post_id}", body=test_log, fetched_at="pipeline")


def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    for a, b in (("“", '"'), ("”", '"'), ("‘", "'"), ("’", "'")):
        text = text.replace(a, b)
    return " ".join(text.split())


def quote_in_source(quote: str, body: str) -> bool:
    if not quote:
        return False
    nb = _normalise(body)
    if _normalise(quote) in nb:
        return True
    try:  # the model saw a JSON-encoded body; it may copy the escapes
        decoded = json.loads(f'"{quote}"')
    except json.JSONDecodeError:
        return False
    return isinstance(decoded, str) and _normalise(decoded) in nb


def discover_claims(
    client: ModelClient, record: PostRecord, sources: list[SourceDoc]
) -> tuple[list[Claim], list[str], bool]:
    hints = [
        c.model_dump(include={"text", "source_url", "in_test_log"}) for c in record.draft.claims
    ]
    variants = [
        {"platform": v.platform.value, "title": v.title, "caption": v.caption}
        for v in record.variants
    ]
    res = client.run(
        spec("P10", "A"),
        f"<title>{record.draft.title}</title>\n<script>{record.draft.script}</script>\n"
        f"<variants>{json.dumps(variants, ensure_ascii=False)}</variants>",
        P10CallAOutput,
        sources=sources,
        user_tail=f"<drafter_claims>{json.dumps(hints, ensure_ascii=False)}</drafter_claims>",
    )
    if res.output is None or res.output.status == "blocked":
        reason = res.output.blocked_reason if res.output else res.stop_reason
        raise AgentBlockedError(f"P10 call A: {reason}")
    by_text = {c.text: c for c in record.draft.claims}
    claims: list[Claim] = []
    for c in res.output.claims:
        prior = by_text.get(c.text)
        claims.append(
            Claim(
                text=c.text,
                source_url=c.source_url or (prior.source_url if prior else None),
                source_quote=prior.source_quote if prior else None,
                in_test_log=prior.in_test_log if prior else False,
            )
        )
    claims += [Claim(text=t) for t in res.output.added_claims]
    return claims, res.output.endorsement_wording, res.output.injection_suspected


def _verify(
    client: ModelClient,
    call: str,
    claim: Claim,
    source: SourceDoc,
    test_log: str,
    fast_moving: bool,
) -> tuple[ClaimStatus, str, bool, bool]:
    """Returns (status, quote, quote_in_source, injection_suspected)."""
    tail = (
        f"<test_log>{test_log}</test_log>\n<claim>{claim.text}</claim>\n"
        f"<fast_moving_topics>{'yes' if fast_moving else 'no'}</fast_moving_topics>\n"
        "Does the source, as written, say this?"
    )
    res = client.run(
        spec("P10", call),
        f"<source_url>{source.url}</source_url>",
        P10VerifyOutput,
        sources=[source],
        user_tail=tail,
    )
    if res.output is None:
        return "unsupported", "", False, False
    if res.output.status == "blocked":
        return "unsupported", "", False, res.output.injection_suspected
    quote = res.output.supporting_quote
    in_source = quote_in_source(quote, source.body)
    status: ClaimStatus = res.output.claim_status
    if status == "supported" and not in_source:
        status = "unsupported"  # a quote that is not in the source is no support
    return status, quote, in_source, res.output.injection_suspected


def check_claim(
    client: ModelClient,
    claim: Claim,
    sources: list[SourceDoc],
    test_log: str = "",
    fast_moving: bool = False,
) -> tuple[ClaimCheckResult, bool]:
    cc = ClaimCheckResult(claim_text=claim.text, source_url=claim.source_url)
    injection = False
    if claim.source_url:
        candidates = [s for s in sources if s.url == claim.source_url]
        if not candidates:
            cc.final = "missing_source"
            cc.note = "cited source is not among the post's sources"
            return cc, injection
    else:
        candidates = list(sources)
    if not candidates:
        cc.final = "missing_source"
        cc.note = "no sources on the post"
        return cc, injection
    seen: list[ClaimStatus] = []
    for src in candidates:
        cc.status_b, quote_b, cc.quote_in_source, inj_b = _verify(
            client, "B", claim, src, test_log, fast_moving
        )
        injection = injection or inj_b
        seen.append(cc.status_b)
        if cc.status_b != "supported":
            continue
        cc.status_c, _quote_c, _in_c, inj_c = _verify(
            client, "C", claim, src, test_log, fast_moving
        )
        injection = injection or inj_c
        if cc.status_c == "supported":
            cc.final = "supported"
            cc.source_url = src.url
            claim.source_url, claim.source_quote = src.url, quote_b
            return cc, injection
        cc.disagreement = True
        cc.note = f"second verifier disagreed on {src.url}: {cc.status_c}"
    if "outdated" in seen:
        cc.final = "outdated"
    elif claim.source_url is None and all(s == "missing_source" for s in seen):
        cc.final = "missing_source"
    else:
        cc.final = "unsupported"
    return cc, injection


def check_facts(
    record: PostRecord, client: ModelClient, sources: list[SourceDoc], test_log: str = ""
) -> tuple[FactsGate, bool]:
    all_sources = list(sources)
    if test_log:
        all_sources.append(testlog_source(record, test_log))
    claims, endorsement_wording, injection = discover_claims(client, record, all_sources)
    gate = FactsGate(endorsement_wording=endorsement_wording)
    for claim in claims:
        if claim.in_test_log and claim.source_url is None and test_log:
            claim.source_url = f"{TESTLOG_SCHEME}{record.post_id}"
        cc, inj = check_claim(client, claim, all_sources, test_log, record.brief.fast_moving)
        injection = injection or inj
        claim.status = cc.final
        gate.claim_checks.append(cc)
    record.draft.claims = claims
    gate.checked = len(claims)
    gate.unsupported_claims = [cc.claim_text for cc in gate.claim_checks if cc.final != "supported"]
    gate.unsupported_claims += [f"endorsement wording: {w}" for w in endorsement_wording]
    gate.passed = not gate.unsupported_claims
    return gate, injection
