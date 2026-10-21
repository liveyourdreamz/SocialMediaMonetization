"""The guided tour: one worked Patch episode, idea to published folder, one stop per
stage, with the real content of that stage on screen. The first eight stops read the
record the pipeline produced; the last five are done for real on that record (open
the card, approve, Outbox, publish), so the buttons pressed are the real ones.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cap.demo import CHARACTER_BLOCK, SOURCES, TEST_LOG
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
        "Everything starts with you. From the Operator screen you file a brief: the "
        "channel, the topic, and the material the drafter may use. For Patch that "
        "material is a test log you wrote. Nothing is drafted from memory.",
    ),
    Stop(
        "material",
        "The material: test log and sources",
        "The pipeline fetches each source URL and keeps the page text with its date. "
        "These pages and your test log are the only facts the script may contain; the "
        "facts gate later checks every claim against them.",
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
        "sends it back with instructions. A revise verdict loops once through the "
        "drafter; what the critic said is hidden from you until after your verdict.",
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
        "The script is spoken by Patch's voice, cut into typographic frames in the Proof "
        "palette, and assembled by ffmpeg into the long video, the Short and a "
        "thumbnail. The file's hash is recorded: the approval token will bind to it.",
        needs="asset",
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
        "Finalising issues a signed token bound to the post, the video's hash and your "
        "name. The post waits in the Outbox with a preview of exactly what goes out per "
        "platform. Nothing has left the laptop yet.",
        needs="token",
    ),
    Stop(
        "publish",
        "Publish",
        "The publisher verifies the token and the file hash, refuses canaries and "
        "changed renders, then sends. On the laptop it writes a folder; with a YouTube "
        "channel connected the same step uploads unlisted, labelled as AI content.",
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
    source = SOURCES[0]
    return {
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
        "test_log": TEST_LOG,
        "source": source,
        "character_block": CHARACTER_BLOCK,
        "disclosure": disclosure_block(),
        "script_parts": record.draft.script.split("[[HEMANT_OPENING_LINE]]"),
        "pkg": pkg or {},
        "done_through": sum(
            1 for s in STOPS if not s.needs or s.needs in have
        ),  # stops whose content exists
    }
