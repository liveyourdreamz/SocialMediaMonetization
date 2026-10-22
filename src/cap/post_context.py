"""Every value the prompts expect, filled in by code and sent with every call.

22 Oct 2026: three real briefs in a row stopped because a prompt expected an input the
code never sent (a speaking rate, what the opening-line token is, then P7's length,
limits and sponsor status). A model told to "keep within the supplied limits" with no
limits supplied correctly refuses. This block supplies them all, from the brief and
the settings, so no prompt has to guess. It carries facts about the post only, never
another agent's notes (D26 still holds for the judges).
"""

from __future__ import annotations

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


def post_context(record: PostRecord) -> str:
    from cap.config import get_settings

    s = get_settings()
    ch = record.channel_id.value
    character, language, fmt = CHANNELS.get(ch, ("", "", record.brief.format_id))
    sponsored = record.sponsor is not None
    limits = "\n".join(f"  {k}: {v}" for k, v in PLATFORM_LIMITS.items())
    return f"""<post_context>
These values are set by code for this post. Treat them as given; do not report any of them as missing.
channel: {ch} ({character}, {language})
format: {fmt} (format_id {record.brief.format_id})
length_s: {record.brief.length_s} (target spoken length of the full episode)
sponsored: {"yes, the Ad prefix and spoken disclosure apply" if sponsored else "no; no Ad prefix applies"}
affiliate links: none
claim limit: at most {s.max_claims} factual claims in the post
test log: given in this message when there is one (Patch only); otherwise none, which is normal
exemplars: none yet for this channel; write from the character block
sourcing: the script is written from the model's knowledge; sources are found and each claim is checked after drafting (D41), so a claim without a source yet is expected at this stage, not a defect
opening line: [[HEMANT_OPENING_LINE]] marks where the approver's own line goes; it is intentional
platform limits:
{limits}
</post_context>"""
