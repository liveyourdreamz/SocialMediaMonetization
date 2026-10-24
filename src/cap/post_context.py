"""Every value the prompts expect, filled in by code and sent with every call.

22 Oct 2026: three real briefs in a row stopped because a prompt expected an input the
code never sent (a speaking rate, what the opening-line token is, then P7's length,
limits and sponsor status). A model told to "keep within the supplied limits" with no
limits supplied correctly refuses. This block supplies them, from the brief and the
settings, so no prompt has to guess.

Two forms (regression run, 22 Oct): the writing steps (drafter, critic, adapter) get the
values as instructions to work to. The checking steps (the ethics judges, the
made-for-kids assessor, claim discovery) get them as what the brief recorded, never as
facts that override the content: a judge told "sponsored: no" must still judge the
sponsor segment it can see, and nothing here relaxes a rule that needs a visible
source (R-09). It carries facts about the post only, never another agent's notes
(D26 still holds for the judges).
"""

from __future__ import annotations

from typing import Literal

from cap.schema.post_record import PostRecord

CHANNELS = {
    "A": ("Patch", "English", "Take it apart"),
    "B": ("Falak", "English", "From orbit"),
    "C": ("Falak", "Modern Standard Arabic", "From orbit"),
}

# Platform limits the adapter and the judges work to (YouTube's published limits; the
# Short is kept to 60 s and vertical so the same file also suits Reels and TikTok).
PLATFORM_LIMITS = {
    "youtube_long": "title at most 100 characters; description at most 5000 characters; "
    "duration = the full episode",
    "youtube_short": "title at most 100 characters; duration at most 60 seconds; vertical 9:16",
}

Role = Literal["writer", "checker"]


def post_context(record: PostRecord, role: Role = "writer") -> str:
    from cap.config import get_settings
    from cap.domain.disclosure import is_paid

    s = get_settings()
    ch = record.channel_id.value
    character, language, fmt = CHANNELS.get(ch, ("", "", record.brief.format_id))
    fmt_line = f"{fmt} (format_id {record.brief.format_id})" if record.brief.format_id else fmt
    limits = "\n".join(f"  {k}: {v}" for k, v in PLATFORM_LIMITS.items())
    common = f"""channel: {ch} ({character}, {language})
format: {fmt_line}
length_s: {record.brief.length_s} (target spoken length of the full episode)
test log: given in this message when there is one (Patch only); otherwise none, which is normal
opening line: [[HEMANT_OPENING_LINE]] marks where the approver's own line goes; it is intentional
platform limits:
{limits}"""
    if role == "checker":
        sponsor = (
            "the brief records a sponsor; the Ad rules apply"
            if record.sponsor is not None or record.brief.sponsor_id
            else "the brief records no sponsor"
        )
        paid = (
            " Code found sponsorship or affiliate wording in the content."
            if (record.sponsor is None and is_paid(record))
            else ""
        )
        return f"""<post_context>
What the brief and the code recorded for this post. It tells you what the post was meant to be; it never overrides what the content shows. Where the content contradicts a line here, assess the content as it is and say so; do not block or escalate only because a line here is absent or different.
{common}
sponsor: {sponsor}.{paid} If the content itself shows sponsorship, affiliate links or a paid placement, assess what it shows.
sources: a separate facts gate checks each claim against sources; this relaxes no rule that requires a visible source in the content or its source list.
</post_context>"""
    sponsored = record.sponsor is not None or bool(record.brief.sponsor_id)
    return f"""<post_context>
These values are set by code for this post. Treat them as given; do not report any of them as missing.
{common}
sponsored: {"yes, the Ad prefix and spoken disclosure apply" if sponsored else "no; no Ad prefix applies"}
affiliate links: none
claim limit: at most {s.max_claims} factual claims in the post
claims allowed: {"; ".join(record.brief.claims_allowed) or "the mechanism and commonly cited figures"}
risk lane: {record.brief.lane.value}{" (the topic touches a sensitive area: explain, never advise)" if record.brief.lane.value == "high" else ""}
exemplars: none yet for this channel; write from the character block
sourcing: the script is written from the model's knowledge; sources are found and each claim is checked after drafting (D41), so a claim without a source yet is expected at this stage, not a defect
</post_context>"""
