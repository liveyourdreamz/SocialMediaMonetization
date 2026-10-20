"""Demo posts for `cap serve` and the UI tests: one run of the real pipeline on mock
fixtures per post, then a few fields adjusted so the queue shows three channels and a
canary. The analytics block is marked "example figures" and the Portfolio shows the
mark (D39): nothing here pretends to be a measurement."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cap.approval.app import Store
from cap.approval.card import prepare_card
from cap.demo import CHARACTER_BLOCK, SOURCES, TEST_LOG, happy_fixtures
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


def demo_post(channel: Channel = Channel.A, canary: bool = False) -> PostRecord:
    from cap.config import get_settings

    rec = PostRecord(
        channel_id=channel,
        brief=Brief(topic="Notely 7-day test", anchor_elements=["test log"]),
    )
    deps = PipelineDeps(
        client=MockClient(happy_fixtures()),
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
    """The record a brief starts as, visible on the Operator screen while it runs."""
    ch = Channel(channel)
    rec = PostRecord(channel_id=ch, brief=Brief(topic=topic, anchor_elements=["test log"]))
    rec.status = PostStatus.DRAFTING
    rec.log(f"brief filed: {topic}")
    return rec


def run_brief(
    store: Store,
    channel: str,
    topic: str,
    *,
    urls: list[str] | None = None,
    test_log: str = "",
    record: PostRecord | None = None,
) -> PostRecord:
    """The whole pipeline on one brief. Mock backend: the sample episode with the brief's
    topic recorded; Anthropic backend: a real draft of the topic from the fetched
    sources and the test-log notes."""
    from cap.config import get_settings
    from cap.fetch import fetch_sources
    from cap.models import get_client

    rec = record or new_brief(channel, topic)
    ch = rec.channel_id
    sources = fetch_sources(urls) if urls else SOURCES
    if urls:
        rec.log(f"fetched {len(sources)} source(s)")
    client = get_client(happy_fixtures())
    deps = PipelineDeps(
        client=client,
        anchor_elements={ch.value: ["test log"]},
        character_blocks={ch.value: CHARACTER_BLOCK},
        test_logs={rec.post_id: test_log.strip() or TEST_LOG},
        render_dir=Path(get_settings().data_dir) / "renders",
    )
    out = run_post(rec, sources, deps)
    if get_settings().model_backend.value == "mock":
        out.log("drafted by the mock backend from the sample episode (no API key yet)")
    if ch == Channel.C and get_settings().model_backend.value == "mock":
        out = _falak_arabic(out)
        if out.status == PostStatus.AWAITING_APPROVAL:
            prepare_card(out)
    store.put(out)
    return out


def seed_store(store: Store, demo: bool = False, canary: bool = False) -> None:
    a = demo_post(Channel.A)
    a.created_at = datetime.now(UTC) - timedelta(days=2)
    store.put(a)
    if demo:
        b = prepare_card(_falak_english(demo_post(Channel.B)))
        b.created_at = datetime.now(UTC) - timedelta(days=1)
        store.put(b)
        c = prepare_card(_falak_arabic(demo_post(Channel.C)))
        store.put(c)
        store.analytics = EXAMPLE_ANALYTICS
        store.analytics_note = "example figures"
    if canary:
        store.put(demo_post(Channel.A, canary=True))
