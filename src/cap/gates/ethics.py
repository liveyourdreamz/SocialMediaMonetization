"""Gate 8: ethics and compliance (PRD 4.4, Prompt Pack P1, D26, D32).

Order of operations, all in code:
1. Hard rules marked judged_by: code run first (R-05 bait patterns, R-07 disclosure
   template, R-12 source list, R-20 is the gate itself). Any block ends the gate: fail.
2. Otherwise the judges run, one call per variant, each judge isolated: judge A
   (Opus 5.5, effort high) and judge B (Sonnet 5.5). Each receives only the content,
   the media manifest, the sponsor record and the rulebook text. Never the other
   judge's output, never Drafter or critic notes.
3. Aggregation per variant: any fail -> fail; else any escalate -> escalate; else pass.
   A judge that is blocked, refused, timed out or disagrees with the other -> escalate.
4. The post verdict is the worst verdict over its variants.

Phase 1 runs two Claude judges; the Gemini judge joins in Phase 2 (D32) as judge C
through the same ModelClient interface.
"""

from __future__ import annotations

import re
from typing import Literal

from cap.agents.outputs import P1Output
from cap.models import ModelClient
from cap.policy import AI_ROLE_DISCLOSURE_KEY
from cap.prompts import spec
from cap.rulebook import Rulebook, load_rulebook
from cap.schema.post_record import (
    EthicsGate,
    JudgeResult,
    Platform,
    PostRecord,
    RuleFinding,
    RuleOutcome,
    Variant,
    Verdict,
)

# R-05 engagement bait: asks framed as a condition or game. Code blocks only the
# unmistakable phrasings, each anchored where an imperative starts (a sentence or clause
# opening, or after "and", "then", "please"...), so ordinary prose about software never
# fires: "the comment section", "like the video editor", "share this post with your
# team", "Comment API calls", "React for beginners". Anything less certain is left to the
# judges, who check R-05 too (judged_by: both). A plain closing "subscribe" is allowed.
_ASK = r"(?:(?:^|(?<=[^\w\s'’]))\s*|\b(?:and|then|so|now|please|just|also)\s+)"
_CAPS = r"[\"“']?(?-i:[A-Z]{2,})[\"”']?"  # case-sensitive: "Comment YES", not "comment on"
_BAIT = re.compile(
    _ASK + r"(?:"
    r"comment\s+(?:(?:yes|no|" + _CAPS + r")\s+(?:if|below|to|for|and)\b"
    r"|(?:down\s+)?below\b|\S+\s+if\s+you\b)"
    r"|type\s+(?:yes|no|" + _CAPS + r")\s+(?:in\s+the\s+(?:comments|chat)|below|if\s+you)\b"
    r"|tag\s+(?:\d+|a|one|two|three|four|five|some|your)\s+(?:friends?|mates?|people|someone)\b"
    r"|react\s+(?:with\s+\S+\s+)?(?:if\s+you\b|for\s+(?:part|the\s+next|more)\b)"
    r"|like\s+(?:(?:this|the)\s+(?:video|post|short|clip)\s+)?if\s+you\b"
    r"|like\s+(?:this|the)\s+(?:video|post|short|clip)\s*(?:[.!]|$|and\s+(?:subscribe|share))"
    r"|share\s+(?:this\s+(?:video|post|short|clip)\s+)?if\s+you\b"
    r"|vote\s+(?:(?:yes|no)\s+(?:in|below|if)\b|in\s+the\s+comments\b)"
    r")"
    r"|\bsmash\s+(?:that|the)\s+like\b",
    re.I | re.M,
)


def hard_rules(record: PostRecord, variant: Variant, rulebook: Rulebook) -> list[RuleFinding]:
    """Rules judged by code. Returns findings; an empty list means none triggered."""
    findings: list[RuleFinding] = []
    # one line per surface: each starts where an ask could start
    text = "\n".join(
        [
            variant.title,
            variant.caption,
            variant.script_cut or record.draft.script,  # the long-form cut is the script
            *(variant.on_screen_text or record.draft.on_screen_text),
            variant.thumbnail_text or record.draft.thumbnail_text,
        ]
    )
    m = _BAIT.search(text)
    if m:
        findings.append(
            RuleFinding(rule_id="R-05", outcome=RuleOutcome.BLOCK, evidence_span=m.group(0).strip())
        )
    # R-07 is checked per variant: each variant publishes with its own caption.
    if AI_ROLE_DISCLOSURE_KEY not in variant.caption.lower():
        findings.append(
            RuleFinding(
                rule_id="R-07",
                outcome=RuleOutcome.BLOCK,
                evidence_span=f"{variant.platform.value} caption lacks the AI-role disclosure",
            )
        )
    # R-12: a post with factual claims carries a source list. After D41 the facts gate
    # runs first, so the list holds only links that confirmed a claim; a test-log claim
    # counts as sourced. Fires when claims exist and not one of them was confirmed.
    # In flag mode (D41, the default) it does not fire: the facts gate owns sourcing and
    # flags every unconfirmed claim on the card for Hemant; rejecting here would undo
    # that choice (22 Oct: a post whose research found nothing was rejected by R-12).
    from cap.config import get_settings

    confirmed = any(c.status == "supported" for c in record.draft.claims)
    if (
        get_settings().fact_mode == "block"
        and record.draft.claims
        and not record.draft.source_list
        and not confirmed
    ):
        findings.append(
            RuleFinding(
                rule_id="R-12",
                outcome=RuleOutcome.REVISE,
                evidence_span="claims present, no source list",
            )
        )
    return findings


def _judge_user_message(record: PostRecord, variant: Variant) -> str:
    """Only content, manifest and sponsor (D26); the rulebook travels as a static
    system block. The variant text is the pipeline's own output, not fetched text, so
    it is not wrapped as untrusted."""
    sponsor = record.sponsor.model_dump_json() if record.sponsor else "null"
    from cap.post_context import post_context

    return (
        f"{post_context(record, 'checker')}\n"
        f'<variant platform="{variant.platform.value}">\n'
        f"<title>{variant.title}</title>\n"
        f"<script>{variant.script_cut or record.draft.script}</script>\n"
        f"<caption>{variant.caption}</caption>\n"
        f"<on_screen_text>{variant.on_screen_text or record.draft.on_screen_text}</on_screen_text>\n"
        f"<thumbnail_text>{variant.thumbnail_text or record.draft.thumbnail_text}</thumbnail_text>\n"
        f"</variant>\n"
        f"<media_manifest>{record.media_manifest.model_dump_json()}</media_manifest>\n"
        f"<channel>{record.channel_id.value}</channel>\n"
        f"<sponsor>{sponsor}</sponsor>"
    )


Judge = Literal["A", "B", "C"]
_WORST = {Verdict.PASS: 0, Verdict.ESCALATE: 1, Verdict.FAIL: 2}


def run_judge(
    client: ModelClient, judge: Judge, record: PostRecord, variant: Variant, rulebook: Rulebook
) -> JudgeResult:
    sp = spec("P1", judge)
    result = client.run(
        sp,
        _judge_user_message(record, variant),
        P1Output,
        static_blocks=[rulebook.as_prompt_text()],
    )
    model = result.model or sp.model
    if result.output is None:
        status: Literal["refused", "timeout", "blocked"] = (
            "timeout" if result.stop_reason == "error" else "blocked"
        )
        return JudgeResult(
            judge=judge,
            model=model,
            platform=variant.platform,
            verdict=Verdict.ESCALATE,
            status=status,
            explanation=f"no valid output ({result.reason})",
        )
    out = result.output
    if out.status == "blocked":
        refused = (out.blocked_reason or "").startswith("model_refusal")
        return JudgeResult(
            judge=judge,
            model=model,
            platform=variant.platform,
            verdict=Verdict.ESCALATE,
            status="refused" if refused else "blocked",
            explanation=out.blocked_reason or "",
        )
    findings = [
        RuleFinding(rule_id=f.rule_id, outcome=f.outcome, evidence_span=f.evidence_span)
        for f in out.rule_findings
    ]
    verdict = judge_verdict(out.verdict, findings)
    return JudgeResult(
        judge=judge,
        model=model,
        platform=variant.platform,
        verdict=verdict,
        rule_findings=findings,
        explanation=out.explanation,
    )


def judge_verdict(stated: Verdict, findings: list[RuleFinding]) -> Verdict:
    """A judge's outcome is the worse of the verdict it stated and the one its findings
    imply (P1 step 4): a "pass" with a blocking finding fails, and a "fail" or
    "escalate" with no finding to back it is neither trusted nor dropped: it escalates,
    so the approver sees the judge's explanation."""
    computed = verdict_from_findings(findings)
    triggered = any(f.outcome != RuleOutcome.NOT_TRIGGERED for f in findings)
    if stated != Verdict.PASS and not triggered:
        return Verdict.ESCALATE
    return stated if _WORST[stated] > _WORST[computed] else computed


def verdict_from_findings(findings: list[RuleFinding]) -> Verdict:
    """P1 step 4: block -> fail; escalate (no block) -> escalate; revise -> fail (no
    revise path at this gate); nothing -> pass."""
    outcomes = {f.outcome for f in findings}
    if RuleOutcome.BLOCK in outcomes or RuleOutcome.REVISE in outcomes:
        return Verdict.FAIL
    if RuleOutcome.ESCALATE in outcomes:
        return Verdict.ESCALATE
    return Verdict.PASS


def aggregate(judges: list[JudgeResult]) -> tuple[Verdict, bool]:
    verdicts = {j.verdict for j in judges}
    disagreement = len(verdicts) > 1
    if Verdict.FAIL in verdicts:
        return Verdict.FAIL, disagreement
    if Verdict.ESCALATE in verdicts or disagreement or any(j.status != "ok" for j in judges):
        return Verdict.ESCALATE, disagreement
    return Verdict.PASS, False


def check_ethics(
    record: PostRecord,
    client: ModelClient,
    rulebook: Rulebook | None = None,
    judges: tuple[Judge, ...] = ("A", "B"),
) -> EthicsGate:
    rulebook = rulebook or load_rulebook()
    variants = record.variants or [
        Variant(
            platform=Platform.YOUTUBE_LONG, title=record.draft.title, caption=record.draft.caption
        )
    ]
    gate = EthicsGate()
    worst = Verdict.PASS
    for variant in variants:
        hard = hard_rules(record, variant, rulebook)
        gate.hard_rule_findings.extend(hard)
        hard_verdict = verdict_from_findings(hard)
        if hard_verdict == Verdict.FAIL:
            worst = Verdict.FAIL
            break  # PRD 4.4: a hard-rule fail ends the gate with no judge call
        results = [run_judge(client, j, record, variant, rulebook) for j in judges]
        gate.judges.extend(results)
        v, dis = aggregate(results)
        gate.disagreement = gate.disagreement or dis
        if _WORST[v] > _WORST[worst]:
            worst = v
    gate.verdict = worst
    all_findings = gate.hard_rule_findings + [f for j in gate.judges for f in j.rule_findings]
    gate.rule_ids = sorted(
        {f.rule_id for f in all_findings if f.outcome != RuleOutcome.NOT_TRIGGERED}
    )
    gate.evidence_spans = [f.evidence_span for f in all_findings if f.evidence_span]
    return gate
