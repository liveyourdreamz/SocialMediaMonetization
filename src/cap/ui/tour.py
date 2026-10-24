"""The guided tour: one worked Patch episode, idea to published folder, one stop per
stage, with the real content of that stage on screen. The first eight stops read the
record the pipeline produced; the last five are done for real on that record (open
the card, approve, Outbox, publish), so the buttons pressed are the real ones.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cap.characters import CHARACTER_BLOCKS
from cap.demo import tour_example as ex
from cap.policy import disclosure_block
from cap.schema.post_record import PostRecord, PostStatus

RUNNING = ("drafting", "gated", "rendering")


@dataclass(frozen=True)
class Stop:
    key: str
    title: str
    what: str  # what happens at this stage, one short paragraph
    needs: str = ""  # record state the stop needs: "", "draft", "gates", "asset", "card",
    # "verdict", "token", "published"


STOPS: list[Stop] = [
    Stop(
        "idea",
        "The idea becomes a brief",
        "Everything starts with a topic. From the Operator screen you file a brief: the "
        "channel and the topic; for Patch, the test log you wrote. The system finds and "
        "reads the sources itself. Nothing is drafted from memory. The worked example is "
        "a seven-day test of Driftpad, an AI meeting-notes tool. Driftpad is fictional: a "
        "real product would need a real test, and every number Patch speaks has to trace "
        "to a log or a page.",
    ),
    Stop(
        "material",
        "The material: test log and sources",
        "The research scout searches the source library for the topic (agencies, "
        "references and journals for Falak; vendor pages for Patch), fetches each page "
        "and keeps its text with its date. These pages and your test log are the only "
        "facts the script may contain; the facts gate later checks every claim against "
        "them. A URL pinned on the brief is added to what the scout finds.",
    ),
    Stop(
        "character",
        "Who is speaking",
        "The drafter is given Patch's character block from the Anchor Registry and the "
        "disclosure rules. It says who Patch is, how it talks, its signature format, "
        "and what it never does. This is the same text every episode.",
    ),
    Stop(
        "draft",
        "Draft, call 1: the script",
        "The drafter writes the title, script, caption, on-screen text and thumbnail "
        "text. It leaves one slot, [[HEMANT_OPENING_LINE]], for the line you write when "
        "you approve: every episode carries a human-written opening.",
        needs="draft",
    ),
    Stop(
        "claims",
        "Draft, call 2: the claims",
        "A second call lists every factual claim in the script with the source it came "
        "from and the exact quote. Code then checks that every number in the script "
        "appears in some claim; a number with no claim parks the post.",
        needs="draft",
    ),
    Stop(
        "critic",
        "The critic",
        "A separate model reads the draft against the rubric and either accepts it or "
        "sends it back with instructions. Here it sent the first draft back: the verdict "
        "came before the evidence. The drafter revised once; what the critic said stays "
        "hidden from you until after your verdict.",
        needs="draft",
    ),
    Stop(
        "cuts",
        "Platform cuts",
        "The adapter makes one variant per platform: the long YouTube episode and a "
        "Short cut from the script's own sentences. A variant that introduces a new "
        "number, or loses the AI-character disclosure, is dropped.",
        needs="draft",
    ),
    Stop(
        "gates",
        "The four gates",
        "Originality (not a copy of a recent episode; the anchor element present), "
        "ethics (two independent judges per variant, both must pass), disclosure (AI "
        "label, ad prefix, made-for-kids score) and facts (two verifiers per claim). "
        "All four must pass or the post never reaches you.",
        needs="gates",
    ),
    Stop(
        "render",
        "Voice and video",
        "Patch is on screen, speaking the script (the lens brightens with the voice) "
        "beside a reconstruction of what each sentence describes: the seven-day strip, "
        "the vendor's claim, the three-task checklist, the stopwatch to 58 seconds "
        "against the promised 30, the 11-of-14 action items with 2 invented, the export "
        "failing twice on day 5, the verdict stamp. Screenshots uploaded with a brief "
        "take the panel instead. ffmpeg assembles the long video, the Short and a "
        "thumbnail. This is the review cut: where your opening line goes it shows a "
        "card saying so, because you have not written it yet.",
        needs="asset",
    ),
    Stop(
        "connections",
        "Your channels: credentials and connections",
        "Before anything can leave the laptop, each platform is connected once on the "
        "Connections screen: you enter the developer-app credentials from that "
        "platform's console (kept only in cap-data on this laptop, never shown back in "
        "full), then press Connect and sign in to your own YouTube, Facebook Page and "
        "Instagram, or TikTok account. The local folder is always a destination.",
    ),
    Stop(
        "card",
        "The queue and the blind card",
        "The post lands in your queue. The card shows the script and the claims but not "
        "the gates, the critic or any planted fault: you judge it blind, so your verdict "
        "is a real second check and not a rubber stamp. Open it and decide.",
        needs="card",
    ),
    Stop(
        "verdict",
        "Your verdict, then the reveal",
        "Approve needs an opening line and a spot check; reject needs one reason chip. "
        "The server's clock measures your time on the card. Only after the verdict are "
        "the gates and the critic revealed, and the record is finalised.",
        needs="verdict",
    ),
    Stop(
        "outbox",
        "The Outbox",
        "Approving renders the final video with your opening line spoken, then issues a "
        "signed token bound to the post, its release manifest (every file, title, "
        "caption and platform flag) and your name. The post waits in the Outbox with a preview of exactly what goes out per "
        "platform and the destinations to choose from: the connected channels are "
        'ticked, the rest say "connect it first". Nothing has left the laptop yet.',
        needs="token",
    ),
    Stop(
        "publish",
        "Publish",
        "This tour post is not sent to YouTube: it is saved to a folder on this laptop. "
        "For a real post, the publisher re-checks your approval, rebuilds the release "
        "manifest from the files on disk and verifies the token against it, refuses "
        "canaries and anything changed, then sends to every destination you ticked, "
        "recording each result or error separately. A real "
        "post goes to your connected channels: YouTube unlisted with the synthetic-media "
        "flag, a Facebook Reel, Instagram when the video has a public URL, TikTok.",
        needs="published",
    ),
]


def _state(record: PostRecord) -> set[str]:
    have = set()
    if record.draft.script:
        have.add("draft")
    if record.gates.ethics.judges or record.gates.facts.checked:
        have.add("gates")
    if record.asset.uri:
        have.add("asset")
    if record.flags.card_prepared:
        have.add("card")
    if record.approval.pre_reveal_decision:
        have.add("verdict")
    if record.approval.token:
        have.add("token")
    if record.status == PostStatus.PUBLISHED:
        have.add("published")
    return have


def context(record: PostRecord, n: int, pkg: dict[str, Any] | None = None) -> dict[str, Any]:
    n = max(1, min(n, len(STOPS)))
    stop = STOPS[n - 1]
    have = _state(record)
    running = record.status.value in RUNNING
    ready = not stop.needs or stop.needs in have
    import re

    script_numbers = sorted(
        set(re.findall(r"\d(?:[\d,.]*\d)?", record.draft.script)),
        key=lambda n: float(n.replace(",", "").split(".")[0]),
    )
    size = ""
    if record.asset.uri:
        from pathlib import Path

        f = Path(record.asset.uri)
        size = f"{f.stat().st_size / 1e6:.1f} MB on disk" if f.exists() else "file missing on disk"
    return {
        "asset_size": size,
        "stop": stop,
        "n": n,
        "total": len(STOPS),
        "prev": n - 1 if n > 1 else None,
        "next": n + 1 if n < len(STOPS) else None,
        "ready": ready,
        "running": running,
        "waiting": running and not ready,  # refresh until the pipeline gets there
        "parked": record.status == PostStatus.PARKED,
        "have": have,
        "test_log": ex.TEST_LOG,
        "sources": ex.SOURCES,
        "tool": ex.TOOL,
        "script_v1": ex.SCRIPT_V1,
        "script_numbers": script_numbers,
        "revised": any("critic: revise" in h for h in record.history),
        "character_block": CHARACTER_BLOCKS["A"],
        "disclosure": disclosure_block(),
        "script_parts": record.draft.script.split("[[HEMANT_OPENING_LINE]]"),
        "pkg": pkg or {},
        "done_through": sum(
            1 for s in STOPS if not s.needs or s.needs in have
        ),  # stops whose content exists
    }
