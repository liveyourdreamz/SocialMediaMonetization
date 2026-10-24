"""Demo posts for `cap serve` and the UI tests: one run of the real pipeline on mock
fixtures per post, then a few fields adjusted so the queue shows three channels and a
canary. The analytics block is marked "example figures" and the Portfolio shows the
mark (D39): nothing here pretends to be a measurement."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cap import pipeline
from cap.demo import CHARACTER_BLOCK, SOURCES, TEST_LOG, happy_fixtures
from cap.domain.card import prepare_card
from cap.gates.history import InMemoryHistory
from cap.jobs import Job, JobKind
from cap.models import MockClient
from cap.pipeline import PipelineDeps, run_post
from cap.schema.post_record import (
    Brief,
    Channel,
    Claim,
    FaultType,
    PlantedFault,
    PostRecord,
    PostStatus,
)
from cap.store import PostStore


def demo_post(channel: Channel = Channel.A, canary: bool = False) -> PostRecord:
    from cap.config import get_settings

    rec = PostRecord(
        channel_id=channel,
        brief=Brief(topic="Notely 7-day test", anchor_elements=["test log"]),
    )
    deps = PipelineDeps(
        client=MockClient(happy_fixtures()),
        history=InMemoryHistory(),  # a demo sample: compared with nothing, never kept
        anchor_elements={channel.value: ["test log"]},
        character_blocks={channel.value: CHARACTER_BLOCK},
        test_logs={rec.post_id: TEST_LOG},
        render_dir=Path(get_settings().data_dir) / "renders",
    )
    out = run_post(rec, SOURCES, deps)
    if canary:
        # A hand-planted fault for the local walkthrough; the real canary writer (P14)
        # plants it before the gates. The reviewer must reject with the matching chip.
        out.draft.title = "Why THEIR note apps keep failing (and ours do not)"
        out.flags.canary = True
        out.flags.canary_fault_type = FaultType.OUT_GROUP_TITLE
        out.flags.planted_fault = PlantedFault(
            type=FaultType.OUT_GROUP_TITLE,
            span="THEIR note apps",
            why_catchable="the title sides against an out-group",
        )
    return out


def _falak_english(rec: PostRecord) -> PostRecord:
    rec.draft.title = "Why desalination costs what it costs"
    rec.draft.thumbnail_text = "SALT. ENERGY. MONEY."
    rec.draft.script = (
        "[[HEMANT_OPENING_LINE]] Every litre of desalinated water carries an energy bill. "
        "Reverse osmosis pushes seawater through a membrane at about 60 bar; the pumps are "
        "where most of the money goes. The Gulf runs around 40 percent of the world's "
        "capacity, so small efficiency gains here are large in absolute terms.\n\n"
        "The test log for this episode measured a bench membrane at 3.1 kilowatt-hours per "
        "cubic metre, which is in the range the operators publish. Here is where the rest "
        "of the cost sits, and what would move it."
    )
    rec.draft.claims = [
        Claim(
            text="Reverse osmosis runs at about 60 bar",
            source_url="https://desal.example/ro-basics",
            source_quote="Seawater RO plants typically operate at feed pressures of 55 to 70 bar",
            status="supported",
        ),
        Claim(
            text="The Gulf runs around 40 percent of the world's desalination capacity",
            source_url="https://desal.example/idra-2025",
            source_quote="GCC states account for roughly 40% of global installed desalination capacity",
            status="supported",
        ),
        Claim(
            text="The bench membrane measured 3.1 kWh per cubic metre",
            source_url=None,
            source_quote="bench run: 3.1 kWh/m3 mean over 10 cycles",
            in_test_log=True,
            status="supported",
        ),
    ]
    rec.sponsor = None
    rec.draft.on_screen_text = ["60 bar", "40% of world capacity", "3.1 kWh per m³"]
    rec.draft.shot_list = [
        "RO membrane bench rig, close",
        "Gulf plant aerial (credited)",
        "Energy bill overlay",
    ]
    rec.variants = []  # the hand-edited demo post keeps no cuts from the sample episode
    return rec


def _falak_arabic(rec: PostRecord) -> PostRecord:
    rec.draft.title = "لماذا تكلف تحلية المياه ما تكلفه"
    rec.draft.thumbnail_text = "ملح. طاقة. مال."
    rec.draft.script = (
        "[[HEMANT_OPENING_LINE]] كل لتر من المياه المحلاة يحمل فاتورة طاقة. يدفع التناضح "
        "العكسي مياه البحر عبر غشاء عند نحو 60 بار، والمضخات هي حيث تذهب معظم الكلفة. "
        "تشغّل دول الخليج نحو 40 في المئة من الطاقة العالمية للتحلية.\n\n"
        "قاس سجل الاختبار لهذه الحلقة غشاءً مخبرياً عند 3.1 كيلوواط ساعة للمتر المكعب، "
        "وهو ضمن النطاق الذي ينشره المشغّلون."
    )
    rec.draft.caption = "المصادر في الوصف. يقدّمها شخصية بالذكاء الاصطناعي."
    rec.draft.on_screen_text = ["60 بار", "40% من الطاقة العالمية", "3.1 كيلوواط ساعة للمتر المكعب"]
    rec.variants = []
    rec.draft.claims = [
        Claim(
            text="يعمل التناضح العكسي عند نحو 60 بار",
            source_url="https://desal.example/ro-basics",
            source_quote="Seawater RO plants typically operate at feed pressures of 55 to 70 bar",
            status="supported",
        ),
        Claim(
            text="تشغّل دول الخليج نحو 40 في المئة من طاقة التحلية العالمية",
            source_url="https://desal.example/idra-2025",
            source_quote="GCC states account for roughly 40% of global installed desalination capacity",
            status="supported",
        ),
    ]
    return rec


EXAMPLE_ANALYTICS: dict[str, Any] = {
    "source": "YouTube Analytics API (example)",
    "pulled_at": "18 Oct 06:00 GST",
    "portfolio": {
        "cost_per_post_usd": 38.40,
        "cost_note": "$11 models and media · $27 your time",
        "cost_source": "costs: cap ledger (example)",
        "revenue_source": "AdSense: no payout yet",
    },
    "channels": {
        "A": {
            "status": "live since 8 Sep",
            "avg_percent_viewed": 41,
            "watch_hours_12mo": 1260,
            "subscribers": 412,
            "pace_note": "at this pace, threshold in month 11",
        },
        "B": {
            "status": "live since 8 Sep",
            "avg_percent_viewed": 47,
            "watch_hours_12mo": 2040,
            "subscribers": 688,
            "pace_note": "at this pace, threshold in month 9",
        },
    },
}


def new_brief(channel: str, topic: str) -> PostRecord:
    """The record a brief starts as, visible on the Operator screen while it runs: the
    brief built by rule (cap.domain.brief: format, claims scope, risk lane)."""
    from cap.characters import ANCHOR_ELEMENTS
    from cap.config import get_settings
    from cap.domain.brief import build_brief

    ch = Channel(channel)
    brief = build_brief(
        ch.value,
        topic,
        anchor_elements=ANCHOR_ELEMENTS[ch.value],
        length_s=get_settings().video_length_s,
    )
    rec = PostRecord(channel_id=ch, brief=brief)
    if brief.lane.value == "high":
        rec.log("high-risk lane: the topic touches a sensitive area; longer on the card")
    rec.status = PostStatus.DRAFTING
    rec.log(f"brief filed: {topic}")
    return rec


def run_brief(
    store: PostStore,
    channel: str,
    topic: str,
    *,
    urls: list[str] | None = None,
    test_log: str = "",
    record: PostRecord | None = None,
) -> PostRecord:
    """The whole pipeline on one brief, in this thread (the full check and the tests;
    the app runs the same steps as queued jobs). Mock backend: the sample episode with
    the brief's topic recorded; Anthropic backend: a real draft of the topic from the
    model's own knowledge, any pinned page and the test log, then sources found for its
    claims (D41)."""
    rec = record or new_brief(channel, topic)
    deps, sources = _setup(store, rec, test_log, urls=urls or [])
    out = _finish(run_post(rec, sources, deps))
    store.put(out)
    return out


def _setup(
    store: PostStore,
    rec: PostRecord,
    test_log: str,
    *,
    urls: list[str] | None = None,
) -> tuple[PipelineDeps, list[dict[str, Any]]]:
    """What every step of a brief needs: the model client, the character blocks and
    anchors, the test log, research (real backend) and progress saves. With `urls`
    (the write step) the pinned pages are fetched and returned as the sources."""
    from cap.characters import ANCHOR_ELEMENTS, CHARACTER_BLOCKS
    from cap.config import get_settings
    from cap.fetch import fetch_sources
    from cap.models import get_client

    ch = rec.channel_id
    real = get_settings().model_backend.value != "mock"
    test_log = test_log.strip()
    research = None
    sources: list[dict[str, Any]] = []
    if real:
        # D41: the drafter writes from the topic and its own knowledge (plus any pinned
        # page and the test log); the research step then looks for a source for each
        # claim it made, and the facts gate checks them before Hemant sees the card.
        from cap.characters import KNOWLEDGE_NOTE
        from cap.fetch import failed_reason
        from cap.research import find_sources

        if urls:
            fetched = fetch_sources(urls)
            for s in fetched:
                why = failed_reason(s)
                if why:
                    rec.log(f"could not fetch {s['url']}: {why}")
            sources = [s for s in fetched if not failed_reason(s)]
            rec.log(f"fetched {len(sources)} of {len(urls)} pinned source(s)")
        rec.brief.anchor_elements = ANCHOR_ELEMENTS[ch.value][:2]
        blocks = {k: v + "\n\n" + KNOWLEDGE_NOTE for k, v in CHARACTER_BLOCKS.items()}
        anchors = ANCHOR_ELEMENTS
        logs = {rec.post_id: test_log} if test_log else {}
        topic = rec.brief.topic

        def research(drafted: PostRecord):  # type: ignore[no-untyped-def]
            claims = [c.text for c in drafted.draft.claims if not c.in_test_log]
            found, lines = find_sources(topic, ch.value, claims)
            return found, [f"research: looking for sources for {len(claims)} claim(s)", *lines]

    else:
        sources = fetch_sources(urls) if urls else SOURCES
        blocks, anchors = {ch.value: CHARACTER_BLOCK}, {ch.value: ["test log"]}
        logs = {rec.post_id: test_log or TEST_LOG}
    deps = PipelineDeps(
        client=get_client(happy_fixtures()),
        history=store.history(),  # where approvals are written (review R1)
        anchor_elements=anchors,
        character_blocks=blocks,
        test_logs=logs,
        render_dir=Path(get_settings().data_dir) / "renders",
        research=research,
        on_progress=store.put,
    )
    return deps, sources


def _finish(out: PostRecord) -> PostRecord:
    """After the last step that ran: the mock backend says so on the record, and in
    mock mode channel C gets the Arabic sample (the drafter wrote English)."""
    from cap.config import get_settings

    if get_settings().model_backend.value != "mock":
        return out
    out.log("drafted by the mock backend from the sample episode (no API key yet)")
    if out.channel_id == Channel.C:
        out = _falak_arabic(out)
        if out.status == PostStatus.AWAITING_APPROVAL:
            prepare_card(out)
    return out


# ---- the brief's three steps as queued jobs (cap.worker runs them) -------------------


def run_write_job(store: PostStore, job: Job) -> None:
    """BRIEF: fetch any pinned pages, write the script and variants; queue the checks
    with the sources they need."""
    test_log = str(job.payload.get("test_log", ""))
    rec = store.get(job.post_id)
    deps, sources = _setup(store, rec, test_log, urls=list(job.payload.get("urls", [])))
    rec = pipeline.write(rec, pipeline.as_sources(sources), deps)
    _next(store, rec, JobKind.CHECKS, {"test_log": test_log, "sources": sources})


def run_checks_job(store: PostStore, job: Job) -> None:
    """CHECKS: research and the four gates; queue the render unless the post stopped."""
    test_log = str(job.payload.get("test_log", ""))
    rec = store.get(job.post_id)
    deps, _ = _setup(store, rec, test_log)
    sources = pipeline.as_sources(list(job.payload.get("sources", [])))
    rec = pipeline.checks(rec, sources, deps)
    _next(store, rec, JobKind.RENDER, {"test_log": test_log})


def run_render_job(store: PostStore, job: Job) -> None:
    """RENDER: the review cut; the post reaches the approval queue."""
    rec = store.get(job.post_id)
    deps, _ = _setup(store, rec, str(job.payload.get("test_log", "")))
    store.put(_finish(pipeline.render(rec, deps)))


def _next(store: PostStore, rec: PostRecord, kind: JobKind, payload: dict[str, Any]) -> None:
    if pipeline.stopped(rec):
        store.put(_finish(rec))
        return
    store.put(rec)
    store.jobs.enqueue(kind, rec.post_id, payload)


def begin_tour(store: PostStore) -> PostRecord:
    """The record the guided tour follows: the worked Patch example (Notely, 7 days),
    drafted from the fixtures whatever the model backend, rendered by the real renderer.
    Returns at once with the record in the store as 'drafting' and a TOUR job queued;
    the tour pages refresh until each stage is there."""
    from cap.demo import tour_example as ex

    rec = PostRecord(
        channel_id=Channel.A,
        brief=Brief(topic=ex.TOPIC, anchor_elements=["test log"]),
    )
    rec.status = PostStatus.DRAFTING
    rec.flags.tour = True
    rec.log(f"guided tour started: the worked example, Patch tests {ex.TOOL} for 7 days")
    store.put(rec)
    store.jobs.enqueue(JobKind.TOUR, rec.post_id)
    return rec


def run_tour(store: PostStore, job: Job) -> None:
    """The worker's handler for a TOUR job."""
    from cap.characters import ANCHOR_ELEMENTS, CHARACTER_BLOCKS
    from cap.config import get_settings
    from cap.demo import tour_example as ex

    rec = store.get(job.post_id)
    deps = PipelineDeps(
        client=MockClient(ex.fixtures()),
        history=InMemoryHistory(),  # the worked example is never compared or kept
        anchor_elements={"A": ANCHOR_ELEMENTS["A"]},
        character_blocks={"A": CHARACTER_BLOCKS["A"]},
        test_logs={rec.post_id: ex.TEST_LOG},
        render_dir=Path(get_settings().data_dir) / "renders",
        on_progress=store.put,
    )
    out = run_post(rec, ex.SOURCES, deps)
    out.log("drafted from the worked example (fixtures), not the live model")
    store.put(out)


def seed_store(store: PostStore, demo: bool = False, canary: bool = False) -> None:
    a = demo_post(Channel.A)
    a.created_at = datetime.now(UTC) - timedelta(days=2)
    store.put(a)
    if demo:
        b = prepare_card(_falak_english(demo_post(Channel.B)))
        b.created_at = datetime.now(UTC) - timedelta(days=1)
        store.put(b)
        c = prepare_card(_falak_arabic(demo_post(Channel.C)))
        store.put(c)
        store.set_analytics(EXAMPLE_ANALYTICS, "example figures")
    if canary:
        store.put(demo_post(Channel.A, canary=True))
