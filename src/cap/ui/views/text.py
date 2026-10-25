"""Words and small formats every screen uses: labels, dates, durations, channel names."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from cap.config import get_settings
from cap.render import OPENING_SLOT
from cap.schema.post_record import PostRecord, ReasonCode

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
        "no_quote": "unconfirmed: no source found for this claim; judge it yourself",
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
        "no_quote": "غير مؤكَّد: لم يُعثر على مصدر لهذا الادعاء؛ احكم عليه بنفسك",
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
    """'8 Dec 2026' without a leading zero, on every platform (Windows' strftime rejects
    the dash flag). A stored time (UTC) is shown in this computer's time zone."""
    if isinstance(d, datetime) and d.tzinfo is not None:
        d = d.astimezone()
    out = f"{d.day} {d.strftime('%b')}"
    if year:
        out += f" {d.year}"
    if time:
        out += f" {d.strftime('%H:%M')}"
    return out


def log_line(line: str) -> tuple[str, str]:
    """A history line ("<ISO time> <event>") as (local HH:MM:SS, event)."""
    when, _, what = line.partition(" ")
    try:
        return datetime.fromisoformat(when).astimezone().strftime("%H:%M:%S"), what
    except ValueError:
        return "", line


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


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    year, month = d.year + y, m + 1
    last = (date(year + (month == 12), (month % 12) + 1, 1) - timedelta(days=1)).day
    return date(year, month, min(d.day, last))
