"""The queue rows and the card: blind first (D33), the gate lines after the verdict."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cap.approval.app import card_for
from cap.approval.identity import Reviewer
from cap.config import get_settings
from cap.schema.post_record import PostRecord, PostStatus
from cap.store import PostStore
from cap.ui.views.text import (
    LABELS,
    REASON_CHIPS,
    REASON_TEXT,
    about_minutes,
    channel_label,
    is_rtl,
    minutes,
    script_paragraphs,
    word_count,
)


@dataclass
class QueueItem:
    post_id: str
    channel: str
    channel_label: str
    rtl: bool
    title: str
    format: str
    reading: str
    claims: int
    clips: int
    sponsor: str
    stage_one: str | None  # text for channel C, else None
    stage_one_done: bool


def queue_items(records: list[PostRecord], for_role: str, lang: str = "en") -> list[QueueItem]:
    labels = LABELS[lang]
    out = []
    for r in records:
        if for_role == "arabic":
            if not is_rtl(r.channel_id.value) or r.approval.stage_one is not None:
                continue
        fmt = labels["short"] if word_count(r) < 220 else labels["longform"]
        so = r.approval.stage_one
        so_text = None
        if is_rtl(r.channel_id.value) and for_role == "approver":
            so_text = f"stage one: {so.reviewer}" if so else "awaiting stage one"
        out.append(
            QueueItem(
                post_id=r.post_id,
                channel=r.channel_id.value,
                channel_label=channel_label(r.channel_id.value),
                rtl=is_rtl(r.channel_id.value),
                title=r.draft.title,
                format=fmt,
                reading=f"~{max(1, round(r.approval.expected_reading_s / 60))} {labels['read']}",
                claims=len(r.draft.claims),
                clips=len(r.media_manifest.imagery_roles) or len(r.draft.shot_list),
                sponsor=r.sponsor.name if r.sponsor else labels["no_sponsor"],
                stage_one=so_text,
                stage_one_done=so is not None,
            )
        )
    return out


def card_context(
    record: PostRecord, store: PostStore, reviewer: Reviewer, lang: str = "en"
) -> dict[str, Any]:
    card = card_for(record, reviewer)  # the Arabic reviewer never gets gate fields
    labels = LABELS[lang]
    on_card = store.seconds_on_card(record.post_id, reviewer.name)
    gates = None
    if card.gates:
        gates = []
        g = card.gates
        o, e, d, f = g["originality"], g["ethics"], g["disclosure"], g["facts"]
        gates.append(_gate("Originality and anchor", o["pass"], _orig_line(o)))
        gates.append(_gate("Ethics, two judges", e["verdict"] == "pass", _ethics_line(e)))
        gates.append(_gate("Disclosure and labelling", d["pass"], _disc_line(d), near=_mfk_near(d)))
        unconfirmed = any(c.get("final") != "supported" for c in f.get("claim_checks") or [])
        gates.append(_gate("Facts and claims", f["pass"], _facts_line(f), near=unconfirmed))
    media = card.media_manifest
    media_rows = [
        (
            "Clips",
            f"{len(record.draft.shot_list)} in the shot list"
            if record.draft.shot_list
            else "none listed",
        ),
        ("Third-party footage", f"{round(media['third_party_footage_share'] * 100)}%"),
        (
            "Voice",
            f"{get_settings().channel_characters.get(record.channel_id.value, '')}, "
            f"edge-tts neural voice{f' ({record.asset.voice})' if record.asset.voice else ''}",
        ),
        (
            "Synthetic label",
            "on: AI-character label on every frame; provenance signing (C2PA) not yet implemented"
            if media["synthetic_elements_present"]
            else "off",
        ),
        ("Real person shown", "yes" if media["real_person_depicted"] else "no"),
        ("Sponsor", card.sponsor["name"] if card.sponsor else "none"),
    ]
    return {
        "card": card,
        "record": record,
        "L": labels,
        "paragraphs": script_paragraphs(record, labels["slot"]),
        "long_script": len(record.draft.script) > 1800,
        "rtl": lang == "ar",  # the chrome follows the reviewer's language (Proof)
        "content_rtl": is_rtl(record.channel_id.value),  # the post follows its own
        "channel_label": channel_label(record.channel_id.value),
        "format": "Short" if word_count(record) < 220 else "long-form",
        "reading": about_minutes(card.expected_reading_s),
        "on_card": on_card,
        "on_card_text": minutes(on_card),
        "expected_text": f"about {max(1, round(card.expected_reading_s / 60))} min",
        "media_rows": media_rows,
        "chips": REASON_CHIPS,
        "gates": gates,
        "critic": _critic_line(card.critic_issues, record) if card.gates_revealed else None,
        "planted": card.planted_fault,
        "verdict_text": record.approval.pre_reveal_decision.value.capitalize()
        if record.approval.pre_reveal_decision
        else None,
        "reason_text": REASON_TEXT.get(record.approval.reason_code)
        if record.approval.reason_code
        else None,
        "closed": record.status
        not in (PostStatus.AWAITING_APPROVAL,),  # the planted fault shows only then
        "stage_one_word": labels.get(record.approval.stage_one.decision.value, "")
        if record.approval.stage_one
        else "",
        "needs_stage_one": is_rtl(record.channel_id.value) and record.approval.stage_one is None,
    }


def _gate(name: str, passed: bool, line: str, near: bool = False) -> dict[str, Any]:
    state = "pass" if passed and not near else ("near" if passed else "fail")
    prefix = "Pass" if passed and not near else ("Pass, near miss" if passed else "Fail")
    return {"name": name, "state": state, "line": f"{prefix} · {line}"}


def _orig_line(o: dict) -> str:
    anchor = "anchor present" if o["anchor_present"] else "anchor missing"
    return f"nearest post {o['max_similarity']:.2f} similar · skeleton {round(o['skeleton_share'] * 100)}% · {anchor}"


def _ethics_line(e: dict) -> str:
    rules = ", ".join(e.get("rule_ids") or []) or "no rules raised"
    return f"{len(e.get('judges') or [])} judges · {rules}"


def _mfk_near(d: dict) -> bool:
    return d.get("mfk_band") == "clear" and (d.get("mfk_score") or 0) >= 0.25


def _disc_line(d: dict) -> str:
    label = "present" if d.get("ai_label", True) else "missing"
    paid = (
        " · paid promotion, Ad prefix " + ("ok" if d.get("ad_prefix_ok") else "missing")
        if d.get("paid_promo")
        else ""
    )
    return f'made-for-kids {d.get("mfk_score", 0):.2f}, "{d.get("mfk_band", "")}" · AI label {label}{paid}'


def _facts_line(f: dict) -> str:
    checks = f.get("claim_checks") or []
    ok = sum(1 for c in checks if c.get("final") == "supported")
    return f"{ok} of {f.get('checked', len(checks))} supported by a quote in the source"


def _critic_line(issues: list[dict] | None, record: PostRecord) -> str:
    if not issues:
        return "Accepted first time: no issues raised."
    return f"{len(issues)} issue(s) raised, then accepted after one revision: " + "; ".join(
        f"{i.get('severity', '')} {i.get('rubric_item', 'issue')}".strip() for i in issues[:3]
    )
