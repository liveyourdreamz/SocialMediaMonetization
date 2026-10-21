"""What the screens show, computed in code from the store (D24: models never do the
arithmetic). Every figure on the Portfolio carries its source; a figure with no
source is not rendered (Proof, StatTile)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any

from cap import policy
from cap.approval.app import Store, card_for
from cap.approval.identity import Reviewer
from cap.config import get_settings
from cap.schema.post_record import Decision, PostRecord, PostStatus, ReasonCode

OPENING_SLOT = "[[HEMANT_OPENING_LINE]]"

LABELS = {
    "en": {
        "script": "Script",
        "video": "The video you are approving",
        "short_file": "The Short",
        "play": "Press play. The file is what the token binds to; the script below is its narration.",
        "on_screen": "On-screen text",
        "claims": "Claims and where they come from",
        "media": "Media",
        "caption": "Caption",
        "thumb": "Thumbnail text",
        "test_log": "Test log",
        "no_quote": "no quote recorded",
        "stage_one": "Stage one, the Arabic reviewer",
        "slot": "your opening line goes here",
        "claims_n": "claims",
        "clips_n": "clips",
        "no_sponsor": "no sponsor",
        "short": "Short",
        "longform": "long-form",
        "read": "min read",
        "approve": "approve",
        "reject": "reject",
        "escalate": "escalate",
    },
    "ar": {
        "script": "النص",
        "video": "الفيديو الذي توافق عليه",
        "short_file": "المقطع القصير",
        "play": "اضغط تشغيل. هذا الملف هو ما يرتبط به رمز الموافقة؛ النص أدناه هو تعليقه الصوتي.",
        "on_screen": "النص على الشاشة",
        "claims": "الادعاءات ومصادرها",
        "media": "الوسائط",
        "caption": "الوصف",
        "thumb": "نص الصورة المصغّرة",
        "test_log": "سجل الاختبار",
        "no_quote": "لا اقتباس مسجّل",
        "stage_one": "المراجعة الأولى",
        "slot": "سطر هيمانت الافتتاحي يأتي هنا",
        "claims_n": "ادعاءات",
        "clips_n": "مقاطع",
        "no_sponsor": "بلا راعٍ",
        "short": "قصير",
        "longform": "طويل",
        "read": "دقيقة قراءة",
        "approve": "موافقة",
        "reject": "رفض",
        "escalate": "تصعيد",
    },
}


def script_paragraphs(record: PostRecord, slot_text: str) -> list[list[tuple[str, str]]]:
    """Paragraphs as (kind, text) runs: kind 'text', 'line' (Hemant's saved opening
    line) or 'slot' (the drafter's placeholder, not yet written)."""
    out = []
    human = record.human_contribution.text.strip()
    for para in record.draft.script.split("\n\n"):
        if not para.strip():
            continue
        runs: list[tuple[str, str]] = []
        parts = para.split(OPENING_SLOT)
        for i, part in enumerate(parts):
            if i:
                runs.append(("line", human) if human else ("slot", slot_text))
            if part:
                runs.append(("text", part))
        out.append(runs)
    return out


# ReasonChip (Proof): the FaultType list in the reviewer's words, fixed order.
REASON_CHIPS: list[tuple[ReasonCode, str]] = [
    (ReasonCode.UNSOURCED_NUMBER, "A number with no source"),
    (ReasonCode.OUT_GROUP_TITLE, "Title takes a side"),
    (ReasonCode.FIRST_PERSON_HUMAN_CLAIM, "Speaks as a human"),
    (ReasonCode.MISSING_AD_PREFIX, 'Missing "Ad" prefix'),
    (ReasonCode.SPONSOR_VERDICT, "Verdict on a sponsor"),
    (ReasonCode.KIDS_STYLED_VISUAL, "Looks made for kids"),
    (ReasonCode.REUSED_COLD_OPEN, "Reused cold open"),
    (ReasonCode.LANGUAGE, "Language or tone"),
    (ReasonCode.OTHER, "Something else…"),
]
REASON_TEXT = dict(REASON_CHIPS)

GATE_NAMES = [
    ("originality", "Originality and anchor"),
    ("ethics", "Ethics, two judges"),
    ("disclosure", "Disclosure and labelling"),
    ("facts", "Facts and claims"),
]


def day(d: date | datetime, year: bool = True, time: bool = False) -> str:
    """'8 Dec 2026' without a leading zero, on every platform (Windows' strftime rejects the dash flag)."""
    out = f"{d.day} {d.strftime('%b')}"
    if year:
        out += f" {d.year}"
    if time:
        out += f" {d.strftime('%H:%M')}"
    return out


def minutes(seconds: int) -> str:
    m, s = divmod(max(int(seconds), 0), 60)
    return f"{m} min {s:02d} s" if m else f"{s} s"


def about_minutes(seconds: int) -> str:
    return f"~{max(1, round(seconds / 60))} min read"


def channel_label(channel: str) -> str:
    s = get_settings()
    name = s.channel_characters.get(channel, "")
    lang = s.channel_languages.get(channel, "en")
    return f"{channel} · {name}" + (" · عربي" if lang == "ar" else "")


def is_rtl(channel: str) -> bool:
    return get_settings().channel_languages.get(channel, "en") == "ar"


def word_count(record: PostRecord) -> int:
    return len(record.draft.script.split())


# ---------------------------------------------------------------- queue items


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


# ---------------------------------------------------------------- the card


def card_context(
    record: PostRecord, store: Store, reviewer: Reviewer, lang: str = "en"
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
        gates.append(_gate("Facts and claims", f["pass"], _facts_line(f)))
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
            f"{get_settings().channel_characters.get(record.channel_id.value, '')}, ElevenLabs",
        ),
        ("Synthetic label", "on, C2PA signed" if media["synthetic_elements_present"] else "off"),
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


# ---------------------------------------------------------------- portfolio


@dataclass
class Figure:
    label: str
    value: str
    meta: str


@dataclass
class ChannelCard:
    channel: str
    label: str
    rtl: bool
    status: str
    rows: list[tuple[str, str]] = field(default_factory=list)
    progress: float | None = None
    progress_text: str = ""
    note: str = ""


@dataclass
class Portfolio:
    month: str
    note: str
    queue_count: int
    queue_minutes: int
    queue_oldest_days: int
    figures: list[Figure]
    channels: list[ChannelCard]
    gate_title: str
    gate_body: str
    gate_meta: str
    sources: str
    published_count: int = 0
    approved_waiting: int = 0


CLOSED = (PostStatus.APPROVED, PostStatus.REJECTED, PostStatus.PARKED, PostStatus.PUBLISHED)


def _finalised(store: Store) -> list[PostRecord]:
    """Posts a reviewer has closed (not canaries, not pending revisions)."""
    return [p for p in store.posts.values() if p.status in CLOSED and p.approval.decided_at]


def canary_record(store: Store) -> tuple[int, int]:
    caught = sum(1 for p in store.posts.values() if p.flags.canary_outcome == "caught")
    missed = sum(1 for p in store.posts.values() if p.flags.canary_outcome == "missed")
    return caught, missed


def canary_rate(store: Store) -> str:
    """The schedule restarts at a miss (P14): count cards closed since the last one."""
    closed = _finalised(store)
    misses = [
        p.approval.decided_at
        for p in store.posts.values()
        if p.flags.canary_outcome == "missed" and p.approval.decided_at
    ]
    since = max(misses) if misses else None
    reviewed = sum(1 for p in closed if since is None or (p.approval.decided_at or since) > since)
    seen = 0
    for n, m in get_settings().canary_schedule:
        if m == 0 or reviewed < seen + m:
            return f"1 in {n}"
        seen += m
    return f"1 in {get_settings().canary_schedule[-1][0]}"


def portfolio(store: Store) -> Portfolio:
    s = get_settings()
    now = datetime.now(UTC)
    q = store.queue()
    oldest = max(((now - p.created_at).days for p in q), default=0)
    a = store.analytics
    chans: dict[str, dict[str, Any]] = a.get("channels", {})
    port: dict[str, Any] = a.get("portfolio", {})

    # figures
    approved_posts = [
        p for p in store.posts.values() if p.status in (PostStatus.APPROVED, PostStatus.PUBLISHED)
    ]
    if "revenue_usd" in port and approved_posts:
        rev = Figure(
            "Revenue / approved post",
            f"${port['revenue_usd'] / len(approved_posts):,.2f}",
            port.get("revenue_note", ""),
        )
    elif "revenue_usd" in port:
        rev = Figure("Revenue / approved post", "$0.00", "no approved posts in the period")
    else:
        rev = Figure("Revenue / approved post", "—", "no channel monetised yet")
    if "cost_per_post_usd" in port:
        cost = Figure(
            "Cost / approved post", f"${port['cost_per_post_usd']:,.2f}", port.get("cost_note", "")
        )
    else:
        cost = Figure("Cost / approved post", "—", "no cost ledger yet")
    times = [
        p.approval.time_on_card_s
        for p in _finalised(store)
        if p.approval.time_on_card_s > 0 and not p.flags.canary
    ]
    rt = Figure(
        "Your review time",
        minutes(int(median(times))) if times else "—",
        "median per card · target 30" if times else "no cards reviewed yet",
    )
    c, m = canary_record(store)
    can = Figure(
        "Canaries", f"{c} caught · {m} missed", f"rate {canary_rate(store)} · resets at a miss"
    )

    # channels
    cards = []
    for ch in s.channels:
        label = channel_label(ch)
        d = chans.get(ch)
        month_posts = [
            p
            for p in approved_posts
            if p.channel_id.value == ch
            and p.created_at.year == now.year
            and p.created_at.month == now.month
        ]
        longform = sum(1 for p in month_posts if word_count(p) >= 220)
        shorts = len(month_posts) - longform
        card = ChannelCard(ch, label, is_rtl(ch), "")
        if not d:
            card.status = "not live"
            card.note = (
                "Waiting on: the channel, the first pull by agent 14."
                if ch != "C"
                else "Launches in month 3 of B. Waiting on: the Arabic reviewer's name in the allow-list, the Falak Arabic voice test."
            )
            cards.append(card)
            continue
        card.status = d.get("status", "live")
        card.rows = [
            (
                "This month",
                f"{longform} of {s.longform_per_channel_per_month} long-form · {shorts} of {s.shorts_cap_per_channel_per_month} Shorts",
            ),
            (
                "Average % viewed",
                f"{d['avg_percent_viewed']}% (long-form, 28 days)"
                if "avg_percent_viewed" in d
                else "no pull yet",
            ),
            (
                "Watch hours, 12 mo",
                f"{d['watch_hours_12mo']:,} of {policy.YPP_WATCH_HOURS_12MO:,} needed"
                if "watch_hours_12mo" in d
                else "no pull yet",
            ),
            (
                "Subscribers",
                f"{d['subscribers']:,} of {policy.YPP_SUBSCRIBERS:,} needed"
                if "subscribers" in d
                else "no pull yet",
            ),
            ("Monetised", d.get("monetised", "not yet · YPP bar rises 1 Feb 2027")),
        ]
        if "watch_hours_12mo" in d:
            share = min(1.0, d["watch_hours_12mo"] / policy.YPP_WATCH_HOURS_12MO)
            card.progress = share
            pace = d.get("pace_note", "")
            card.progress_text = f"{round(share * 100)}% of the watch-hour threshold" + (
                f" · {pace}" if pace else ""
            )
        cards.append(card)

    # kill gate
    if s.pilot_start:
        start = date.fromisoformat(s.pilot_start)
        gates = [
            (3, _add_months(start, 3)),
            (6, _add_months(start, 6)),
            (9, _add_months(start, 9)),
            (12, _add_months(start, 12)),
        ]
        nxt = next(((n, d) for n, d in gates if d >= now.date()), gates[-1])
        gate_title = f"Month {nxt[0]} review · {day(nxt[1])}"
        gate_body = (
            f"What must be true: [R1 month-{nxt[0]} threshold, re-run for the Feb 2027 YPP bar]."
        )
        gate_meta = f"Pilot started {day(start)}."
    else:
        gate_title, gate_body, gate_meta = (
            "Not started",
            "The kill-gate clock starts the day channels A and B go live (PRD 8).",
            "Set CAP_PILOT_START at go-live.",
        )

    src_parts = []
    src_parts.append(
        f"{a['source']}, pulled {a['pulled_at']}"
        if a.get("source")
        else "YouTube Analytics: no pull yet (agent 14 not running)"
    )
    src_parts.append(port.get("revenue_source", "AdSense: no payout yet"))
    src_parts.append(port.get("cost_source", "costs: no ledger yet"))
    return Portfolio(
        month=now.strftime("%B %Y"),
        note=store.analytics_note,
        queue_count=len(q),
        queue_minutes=round(sum(p.approval.expected_reading_s for p in q) / 60),
        queue_oldest_days=oldest,
        figures=[rev, cost, rt, can],
        channels=cards,
        gate_title=gate_title,
        gate_body=gate_body,
        gate_meta=gate_meta,
        sources=" · ".join(src_parts),
        published_count=sum(1 for p in store.posts.values() if p.status == PostStatus.PUBLISHED),
        approved_waiting=sum(1 for p in store.posts.values() if p.status == PostStatus.APPROVED),
    )


def _add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    year, month = d.year + y, m + 1
    last = (date(year + (month == 12), (month % 12) + 1, 1) - timedelta(days=1)).day
    return date(year, month, min(d.day, last))


# ---------------------------------------------------------------- digest


@dataclass
class Digest:
    week: str
    approved: int
    rejected: int
    escalated: int
    reasons: list[tuple[str, int]]
    other_share: float | None
    other_flag: bool
    critic_flag_rate: float | None
    reviewer_reject_rate: float | None
    agreement: float | None
    revised: int
    canaries_caught: int
    canaries_missed: int
    proposals: list[str]


def digest(store: Store) -> Digest:
    now = datetime.now(UTC)
    since = now - timedelta(days=7)
    week = [
        p
        for p in _finalised(store)
        if p.approval.decided_at and p.approval.decided_at >= since and not p.flags.canary
    ]
    approved = sum(1 for p in week if p.approval.decision == Decision.APPROVE)
    rejected = sum(1 for p in week if p.approval.decision == Decision.REJECT)
    escalated = sum(1 for p in week if p.approval.decision == Decision.ESCALATE)
    counts: dict[str, int] = {}
    for p in week:
        if p.approval.decision in (Decision.REJECT, Decision.ESCALATE) and p.approval.reason_code:
            k = REASON_TEXT[p.approval.reason_code]
            counts[k] = counts.get(k, 0) + 1
    rejects = rejected + escalated
    other = counts.get(REASON_TEXT[ReasonCode.OTHER], 0)
    other_share = other / rejects if rejects else None
    critic = [p for p in week if p.draft.critic_verdict]
    critic_rate = (
        sum(1 for p in critic if p.draft.critic_verdict == "revise") / len(critic)
        if critic
        else None
    )
    pairs = [p for p in week if p.approval.pre_reveal_decision and p.approval.decision]
    agreement = (
        sum(1 for p in pairs if p.approval.pre_reveal_decision == p.approval.decision) / len(pairs)
        if pairs
        else None
    )
    canaries = [
        p
        for p in store.posts.values()
        if p.flags.canary and p.approval.decided_at and p.approval.decided_at >= since
    ]
    return Digest(
        week=f"{day(since, year=False)} to {day(now)}",
        approved=approved,
        rejected=rejected,
        escalated=escalated,
        reasons=sorted(counts.items(), key=lambda kv: -kv[1]),
        other_share=other_share,
        other_flag=bool(other_share and other_share > get_settings().reject_other_share_flag),
        critic_flag_rate=critic_rate,
        reviewer_reject_rate=rejects / len(week) if week else None,
        agreement=agreement,
        revised=sum(1 for p in week if p.approval.revised_after_reveal),
        canaries_caught=sum(1 for p in canaries if p.flags.canary_outcome == "caught"),
        canaries_missed=sum(1 for p in canaries if p.flags.canary_outcome == "missed"),
        proposals=[],  # the learning loop (D35) proposes after the first monetised month
    )


# ---------------------------------------------------------------- operator


def parked(store: Store) -> list[tuple[PostRecord, str]]:
    out = []
    for p in store.posts.values():
        if p.status == PostStatus.PARKED:
            reason = next((h for h in reversed(p.history) if "parked: " in h), "")
            text = reason.split("parked: ", 1)[-1] if reason else "escalated by the reviewer"
            if p.approval.reason_code:
                text = f"{REASON_TEXT[p.approval.reason_code]} · {text}"
            out.append((p, text))
    return out


def published(store: Store) -> list[PostRecord]:
    out = [p for p in store.posts.values() if p.status == PostStatus.PUBLISHED]
    return sorted(out, key=lambda p: p.publish.published_at or p.created_at, reverse=True)


def recent_briefs(store: Store) -> list[PostRecord]:
    """The last few posts in any state, newest first: what a brief turned into."""
    return sorted(store.posts.values(), key=lambda p: p.created_at, reverse=True)[:8]


def outbox(store: Store) -> list[PostRecord]:
    """Approved, token issued, not yet published: what the publisher will send."""
    out = [
        p for p in store.posts.values() if p.status == PostStatus.APPROVED and not p.flags.canary
    ]
    return sorted(out, key=lambda p: p.approval.decided_at or p.created_at)


def package(record: PostRecord) -> dict[str, Any]:
    """Exactly what goes out, per platform, as the adapters will receive it."""
    human = record.human_contribution.text.strip()
    script = record.draft.script.replace(OPENING_SLOT, human) if human else record.draft.script
    base = {
        "title": record.draft.title,
        "caption": record.draft.caption,
        "thumbnail_text": record.draft.thumbnail_text,
        "on_screen_text": list(record.draft.on_screen_text),
        "script": script,
        "shot_list": list(record.draft.shot_list),
        "sources": sorted(
            {
                c.source_url
                for c in record.draft.claims
                if c.source_url and not c.source_url.startswith("testlog://")
            }
        ),
    }
    cuts = []
    for v in record.variants:
        cuts.append(
            {
                "platform": v.platform.value.replace("_", " "),
                "title": v.title or base["title"],
                "caption": v.caption or base["caption"],
                "duration_s": v.duration_s,
                "script": (v.script_cut.replace(OPENING_SLOT, human) if human else v.script_cut)
                or script,
                "on_screen_text": list(v.on_screen_text) or base["on_screen_text"],
                "thumbnail_text": v.thumbnail_text or base["thumbnail_text"],
            }
        )
    if not cuts:  # no platform cuts recorded: the base package is the one cut
        cuts.append({"platform": "youtube long", "duration_s": 0, **base})
    asset = Path(record.asset.uri) if record.asset.uri else None
    return {
        "base": base,
        "cuts": cuts,
        "asset_name": asset.name if asset else "",
        "asset_exists": bool(asset and asset.exists()),
        "sha256": record.asset.sha256,
        "token_present": bool(record.approval.token),
    }


# destination key -> the adapter that carries it (its result and error keys)
_ADAPTER_FOR = {
    "local": "local",
    "youtube_long": "youtube",
    "youtube_short": "youtube_short",
    "facebook": "facebook",
    "instagram": "instagram",
    "tiktok": "tiktok",
}
_ADAPTER_GROUP = {"facebook": "meta", "instagram": "meta"}


def publish_results(record: PostRecord) -> list[dict[str, Any]]:
    """Per chosen destination: sent / written / not sent, with the link or the reason."""
    from cap.connections import DESTINATION_LABELS

    out: list[dict[str, Any]] = []
    pub = record.publish
    for d in pub.destinations or ["local"]:
        key = _ADAPTER_FOR.get(d, d)
        group = _ADAPTER_GROUP.get(d, "")
        pid = pub.platform_post_ids.get(key) or (pub.platform_post_ids.get(group) if group else "")
        url = pub.urls.get(key) or pub.urls.get(d, "")
        err = pub.errors.get(key) or pub.errors.get(d) or (pub.errors.get(group) if group else "")
        if d == "local":
            state, text = ("written", pid) if pid else ("not written", err or "")
        elif pid and not err:
            state, text = "sent", url or pid
        elif err:
            state, text = "not sent", err
        else:
            state, text = "not sent", "no result recorded"
        out.append(
            {
                "key": d,
                "label": DESTINATION_LABELS.get(d, d),
                "state": state,
                "text": text,
                "url": url if state == "sent" else "",
            }
        )
    return out
