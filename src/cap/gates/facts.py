"""Gate 10: fact and claim check (PRD 4.14, Prompt Pack P10 v1.1.1).

Three calls, all through the model client:
* Call A (Sonnet, effort high): claim discovery over the script and the variant titles
  and captions, with the Drafter's claims as a hint. Claims it adds are checked too,
  and so is every Drafter claim it left out: discovery adds, it never removes.
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
the post in block mode and is a card question in flag mode (D41); endorsement wording
fails it in both; a blocked discovery call falls back to the drafter's list (D43);
sources reach the model only through the client's nonce-keyed wrapper.
"""

from __future__ import annotations

import json
import unicodedata

from cap.agents.outputs import P10CallAOutput, P10VerifyOutput
from cap.models import ModelClient, SourceDoc
from cap.policy import TESTLOG_SCHEME
from cap.post_context import post_context
from cap.prompts import spec, tagged
from cap.schema.post_record import Claim, ClaimCheckResult, ClaimStatus, FactsGate, PostRecord

Source = SourceDoc  # the gate's public name for a fetched document


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
        lambda n: (
            f"{post_context(record, 'checker')}\n"
            + tagged(
                n,
                title=record.draft.title,
                script=record.draft.script,
                variants=json.dumps(variants, ensure_ascii=False),
            )
        ),
        P10CallAOutput,
        sources=sources,
        user_tail=lambda n: tagged(n, drafter_claims=json.dumps(hints, ensure_ascii=False)),
    )
    if res.output is None or res.output.status == "blocked":
        # D43: the drafter's list is checked instead; the post does not stop
        why = res.output.blocked_reason if res.output else res.reason
        record.log(f"claim discovery declined ({str(why)[:160]}); checking the drafter's list")
        return [c.model_copy() for c in record.draft.claims], [], False
    claims = merge_claims(
        record.draft.claims,
        [(c.text, c.source_url) for c in res.output.claims]
        + [(t, None) for t in res.output.added_claims],
    )
    return claims, res.output.endorsement_wording, res.output.injection_suspected


_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def claim_key(text: str) -> str:
    """Two claim texts are the same claim when they match after normalisation (case,
    whitespace, quotes, closing punctuation, Arabic-Indic digits: "٣٠" is "30")."""
    return _normalise(text).translate(_DIGITS).casefold().rstrip(" .!?;:,")


def merge_claims(drafted: list[Claim], discovered: list[tuple[str, str | None]]) -> list[Claim]:
    """Discovery's list (text, cited url) merged with the drafter's, which includes the
    claims code added for unlisted numbers. Discovery may add or reword; it never
    removes: every drafter claim is checked whatever call A returned (an empty list
    from call A once passed a post with nothing checked). Deduplicated on the
    normalised text; a drafter claim keeps its source, quote and test-log mark."""
    by_key = {claim_key(c.text): c for c in drafted}
    out: list[Claim] = []
    seen: set[str] = set()
    for text, url in discovered:
        key = claim_key(text)
        if not key or key in seen:
            continue
        seen.add(key)
        prior = by_key.get(key)
        out.append(
            Claim(
                text=prior.text if prior else text,
                source_url=url or (prior.source_url if prior else None),
                source_quote=prior.source_quote if prior else None,
                in_test_log=prior.in_test_log if prior else False,
            )
        )
    for c in drafted:
        key = claim_key(c.text)
        if key and key not in seen:
            seen.add(key)
            out.append(c.model_copy())
    return out


def _verify(
    client: ModelClient,
    call: str,
    claim: Claim,
    source: SourceDoc,
    test_log: str,
    fast_moving: bool,
) -> tuple[ClaimStatus, str, bool, bool]:
    """Returns (status, quote, quote_in_source, injection_suspected)."""

    def tail(nonce: str) -> str:
        return (
            f"<test_log>{test_log}</test_log>\n{tagged(nonce, claim=claim.text)}\n"
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


def _candidates(claim: Claim, sources: list[SourceDoc]) -> tuple[list[SourceDoc], str]:
    """The sources a claim may be checked against, or none with the reason."""
    if claim.source_url and claim.source_url.startswith(TESTLOG_SCHEME):
        # the drafter cites test-log entries as testlog://<anything>; the post has one
        found = [s for s in sources if s.url.startswith(TESTLOG_SCHEME)]
        if found:
            claim.source_url = found[0].url
    elif claim.source_url:
        found = [s for s in sources if s.url == claim.source_url]
    else:
        found = list(sources)
    if claim.source_url and not found:
        return [], "cited source is not among the post's sources"
    if not found:
        return [], "no sources on the post"
    return found, ""


def _final_without_support(claim: Claim, seen: list[ClaimStatus]) -> ClaimStatus:
    """No source confirmed the claim twice: what the verifiers saw decides the status."""
    if "outdated" in seen:
        return "outdated"
    if claim.source_url is None and all(s == "missing_source" for s in seen):
        return "missing_source"
    return "unsupported"


def check_claim(
    client: ModelClient,
    claim: Claim,
    sources: list[SourceDoc],
    test_log: str = "",
    fast_moving: bool = False,
) -> tuple[ClaimCheckResult, bool]:
    """Two independent verifiers per source (B, then C only when B confirms): the first
    source both confirm makes the claim supported, with B's quote. Returns the result
    and whether either verifier suspected an injection."""
    cc = ClaimCheckResult(claim_text=claim.text, source_url=claim.source_url)
    candidates, why = _candidates(claim, sources)
    cc.source_url = claim.source_url
    if not candidates:
        cc.final, cc.note = "missing_source", why
        return cc, False
    injection = False
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
    cc.final = _final_without_support(claim, seen)
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
        if cc.final != "supported":
            # nothing confirmed it: no quote or link may make it look sourced (review H1)
            claim.source_quote = None
            claim.source_url = None
        gate.claim_checks.append(cc)
    record.draft.claims = claims
    gate.checked = len(claims)
    gate.unsupported_claims = [cc.claim_text for cc in gate.claim_checks if cc.final != "supported"]
    gate.unsupported_claims += [f"endorsement wording: {w}" for w in endorsement_wording]
    from cap.config import get_settings

    # PRD 4.14: endorsement wording fails the post in every mode. Unconfirmed claims
    # fail it in block mode; in flag mode (D41) they become one card question
    # (cap.domain.verdict) and the gate itself passes.
    unconfirmed = [cc for cc in gate.claim_checks if cc.final != "supported"]
    flag_mode = get_settings().fact_mode == "flag"
    gate.passed = not endorsement_wording and (flag_mode or not unconfirmed)
    if unconfirmed and flag_mode:
        record.log(f"facts: {len(unconfirmed)} claim(s) unconfirmed, flagged on the card")
    return gate, injection
