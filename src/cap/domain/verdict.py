"""The one home of the gate decision (PRD 3, R-20, D41, D43).

After the four gates have run, a post is in exactly one of three states:

* PASS: every gate passed and nothing is open. It goes to the card as it is.
* QUESTIONS: no gate failed outright, but something only the approver can settle is
  open: an ethics escalation, a doubtful or unscored made-for-kids result, or claims
  the facts gate could not confirm in flag mode (D41). Each becomes one card question;
  the post is publishable once the approver has acknowledged every one.
* REJECT: a gate failed outright (a near-duplicate, an ethics fail, a likely
  made-for-kids post, a missing Ad prefix, an unsupported claim in block mode,
  endorsement wording). Nobody clears it (R-20).

The pipeline, the record's `gates_cleared` (which the publisher checks) and the views
all read this module; none of them restates the rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from cap.policy import TESTLOG_SCHEME
from cap.schema.post_record import Gates, PostRecord, Verdict

MFK_QUESTION_BANDS = ("doubtful", "unscored")
_MAX_QUESTION = 500


class Outcome(StrEnum):
    PASS = "pass"
    QUESTIONS = "questions"
    REJECT = "reject"


@dataclass(frozen=True)
class GateVerdict:
    outcome: Outcome
    questions: tuple[str, ...] = ()  # for the approver; meaningful when not REJECT
    reasons: tuple[str, ...] = ()  # "gate: why", one per outright failure

    @property
    def failed_gates(self) -> tuple[str, ...]:
        return tuple(r.split(":", 1)[0] for r in self.reasons)


def decide(record: PostRecord) -> GateVerdict:
    """The gate outcome for a record whose gates have run."""
    g = record.gates
    reasons = _reasons(g)
    questions = _questions(g)
    if reasons:
        return GateVerdict(Outcome.REJECT, questions, reasons)
    if questions:
        return GateVerdict(Outcome.QUESTIONS, questions)
    return GateVerdict(Outcome.PASS)


def gates_cleared(record: PostRecord) -> bool:
    """Publishable as far as the gates go: no outright failure, and every question
    (the gates' own and any other on the record) acknowledged by the approver."""
    v = decide(record)
    if v.outcome == Outcome.REJECT:
        return False
    open_items = set(v.questions) | set(record.flags.needs_decision)
    return open_items <= set(record.approval.decisions_acknowledged)


def confirmed_sources(record: PostRecord) -> list[str]:
    """The links that confirmed at least one claim: the only links a description or a
    published package may list. The pipeline's own test log is never one."""
    return sorted(
        {
            c.source_url
            for c in record.draft.claims
            if c.status == "supported"
            and c.source_url
            and not c.source_url.startswith(TESTLOG_SCHEME)
        }
    )


def _originality_reason(g: Gates) -> str:
    o, why = g.originality, []
    if o.skeleton_share:
        why.append(f"{o.skeleton_share:.0%} of sentences open like earlier posts")
    if o.nearest_post_id:
        why.append(f"similarity {o.max_similarity:.2f} to {o.nearest_post_id}")
    if not o.anchor_present:
        why.append("character anchor missing")
    return "originality: " + (", ".join(why) or "R-13")


def _reasons(g: Gates) -> tuple[str, ...]:
    out: list[str] = []
    if not g.originality.passed:
        out.append(_originality_reason(g))
    if g.ethics.verdict == Verdict.FAIL:
        rules = ", ".join(g.ethics.rule_ids) or "a judge failed it"
        out.append(f"ethics: {rules}")
    d = g.disclosure
    if not d.ad_prefix_ok:
        out.append("disclosure: paid post without the Ad prefix")
    elif d.mfk_band == "likely":
        out.append("disclosure: likely made for kids")
    elif not d.passed and d.mfk_band not in MFK_QUESTION_BANDS:
        out.append("disclosure: failed")  # fail closed on any state the rule does not name
    f = g.facts
    if not f.passed:
        if f.endorsement_wording:
            out.append("facts: endorsement wording")
        else:
            out.append(f"facts: {len(f.unsupported_claims)} claim(s) not supported")
    return tuple(out)


def _questions(g: Gates) -> tuple[str, ...]:
    out: list[str] = []
    e = g.ethics
    if e.verdict == Verdict.ESCALATE:
        views = [
            f"judge {j.judge} ({j.platform.value}): {j.verdict.value}"
            + (f", {j.explanation[:160]}" if j.explanation else "")
            for j in e.judges
            if j.verdict != Verdict.PASS or j.status != "ok"
        ]
        rules = f" Rules: {', '.join(e.rule_ids)}." if e.rule_ids else ""
        why = ("; ".join(views) or "the judges did not agree") + rules
        out.append(f"Ethics: the judges escalated this. {why}"[:_MAX_QUESTION])
    d = g.disclosure
    if d.mfk_band in MFK_QUESTION_BANDS:
        factors = ", ".join(d.mfk_factors) or "no factor detail"
        out.append(
            f"Made for kids: {d.mfk_band} ({factors}). Approving keeps it 'not made for kids'."
        )
    unconfirmed = [cc.claim_text for cc in g.facts.claim_checks if cc.final != "supported"]
    if unconfirmed and g.facts.passed:
        n = len(unconfirmed)
        listed = "; ".join(unconfirmed)
        out.append(
            f"Facts: {n} claim{'s' if n != 1 else ''} could not be confirmed: {listed}"[
                :_MAX_QUESTION
            ]
        )
    return tuple(out)
