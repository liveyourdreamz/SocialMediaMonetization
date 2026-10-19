"""Demo posts for `cap serve` and the UI tests: one run of the real pipeline on mock
fixtures per post, then a few fields adjusted so the queue shows three channels and a
canary. The analytics block is marked "example figures" and the Portfolio shows the
mark (D39): nothing here pretends to be a measurement."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from cap.approval.app import Store
from cap.approval.card import prepare_card
from cap.demo import CHARACTER_BLOCK, SOURCES, TEST_LOG, happy_fixtures
from cap.models import MockClient
from cap.pipeline import PipelineDeps, run_post
from cap.schema.post_record import Brief, Channel, Claim, FaultType, PlantedFault, PostRecord


def demo_post(channel: Channel = Channel.A, canary: bool = False) -> PostRecord:
    rec = PostRecord(
        channel_id=channel,
        brief=Brief(topic="Notely 7-day test", anchor_elements=["test log"]),
    )
    deps = PipelineDeps(
        client=MockClient(happy_fixtures()),
        anchor_elements={channel.value: ["test log"]},
        character_blocks={channel.value: CHARACTER_BLOCK},
        test_logs={rec.post_id: TEST_LOG},
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
