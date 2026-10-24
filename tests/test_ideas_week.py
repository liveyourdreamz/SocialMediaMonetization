"""One channel's weekly trend research, end to end on recorded data (D48)."""

from __future__ import annotations

import datetime as dt

from cap.ideabook import MemoryIdeaBook
from cap.ideas.demand import DemandSignal, DemandUnavailableError
from cap.ideas.feeds import FeedItem
from cap.ideas.week import Sources, run_week
from cap.models import MockClient
from cap.schema.ideas import Idea, IdeaStatus

TODAY = dt.date(2026, 10, 28)  # a Wednesday: the week is Monday 26 Oct
MON = dt.date(2026, 10, 26)
STORY = FeedItem("Solar module prices fall again", "https://ourworldindata.org/solar-prices",
                 "Prices fell by a tenth.", TODAY, "Our World in Data")  # fmt: skip


def sources(demand_down=False):
    def demand(ch, today):
        if demand_down:
            raise DemandUnavailableError("no en.wikipedia top lists could be read")
        return [DemandSignal("d-01", "Perovskite solar cell", "en.wikipedia", 41_200, 3.1)]

    return Sources(
        demand=demand,
        feeds=lambda ch, today: ([STORY], ["https://www.positive.news/feed/: ConnectError"]),
        wiki_search=lambda q, lang: [("Solar cell", "https://en.wikipedia.org/wiki/Solar_cell")],
        fetch_page=lambda url: "Module prices fell from $2.00 to $0.20 per watt.",
    )


def p4_item(topic, **kw):
    base = {"topic": topic, "angle": "prices fell by nine tenths", "demand_ref": "d-01",
            "evidence_ids": ["r-001"], "timing": "evergreen"}  # fmt: skip
    base.update(kw)
    return base


def judged(**kw):
    base = {"ethics_precheck": "pass", "evidence_check": "ok", "constructive_fit": 0.9,
            "persona_fit": 0.8, "anchor_fit": 0.8, "risk_lane": "low"}  # fmt: skip
    base.update(kw)
    return base


def client(items, judgements):
    return MockClient({
        ("P4", "default"): [
            {"tool_uses": [{"name": "search_allowlisted", "input": {"query": "solar prices"}}]},
            {"tool_uses": [{"name": "fetch_allowlisted", "input": {"id": "r-001"}}]},
            {"items": items},
        ],
        ("P5", "default"): {"items": [{"n": k, **j} for k, j in enumerate(judgements)]},
    })  # fmt: skip


def test_a_week_runs_whole_and_the_digest_holds_the_ranked_ideas():
    book = MemoryIdeaBook()
    items = [p4_item("Why solar panels keep getting cheaper"),
             p4_item("A startup's bottle will end plastic", demand_ref=None),
             p4_item("Cited a page nobody fetched", evidence_ids=["r-999"])]  # fmt: skip
    marks = [judged(), judged(evidence_check="promotional"), judged()]
    report = run_week("A", TODAY, client(items, marks), book, sources())
    shown = book.week("A", MON)
    assert [i.topic for i in shown] == ["Why solar panels keep getting cheaper"]
    assert shown[0].evidence_urls == ["https://ourworldindata.org/solar-prices"]
    assert shown[0].demand_title == "Perovskite solar cell" and shown[0].rank == 1
    held = {
        i.topic: i.held_reason
        for i in book.week("A", MON, shown_only=False)
        if i.status == IdeaStatus.HELD
    }
    assert held == {"A startup's bottle will end plastic": "evidence promotional",
                    "Cited a page nobody fetched": "no primary source"}  # fmt: skip
    assert (report.shown, report.held) == (1, 2)
    assert report.gaps == ["feed https://www.positive.news/feed/: ConnectError"]


def test_outside_text_reaches_the_models_only_inside_the_tag():
    c = client([p4_item("Why solar panels keep getting cheaper")], [judged()])
    run_week("A", TODAY, c, MemoryIdeaBook(), sources())
    p4_call, p5_call = c.calls[0], c.calls[-1]
    assert f'<untrusted_text id="{p4_call.nonce}">"Perovskite solar cell"' in p4_call.user
    assert p5_call.sources and "Why solar panels" in p5_call.sources[0].body
    assert "Why solar panels" not in p5_call.user  # the topic is not in the trusted message


def test_wikimedia_down_is_a_gap_not_a_stop():
    report = run_week("A", TODAY, client([p4_item("Why solar panels keep getting cheaper",
                      demand_ref=None)], [judged()]), MemoryIdeaBook(), sources(demand_down=True))  # fmt: skip
    assert report.shown == 1 and report.gaps[0].startswith("demand: no en.wikipedia")


def test_last_weeks_unpicked_ideas_expire_when_the_new_week_lands():
    book = MemoryIdeaBook()
    old = Idea(channel="A", week=MON - dt.timedelta(days=7), rank=1, topic="How tides work")
    book.add([old])
    run_week("A", TODAY, client([p4_item("Why solar panels keep getting cheaper")], [judged()]),
             book, sources())  # fmt: skip
    assert book.get(old.idea_id).status == IdeaStatus.EXPIRED


def test_scoring_that_skips_an_item_stops_the_week_visibly():
    book = MemoryIdeaBook()
    report = run_week(
        "A", TODAY, client([p4_item("One"), p4_item("Two")], [judged()]), book, sources()
    )
    assert report.blocked == "idea scoring did not return one judgement per candidate"
    assert book.week("A", MON, shown_only=False) == []


# ---- review R11 ------------------------------------------------------------------------


def test_judgements_are_matched_by_number_not_position():
    """A reply in another order must not give the Red verdict to the wrong topic."""
    items = [p4_item("Why solar panels keep getting cheaper"),
             p4_item("Why country X is sabotaging the water")]  # fmt: skip
    marks = [{"n": 1, **judged(ethics_precheck="red")}, {"n": 0, **judged()}]  # reordered
    book = MemoryIdeaBook()
    run_week("A", TODAY, client(items, marks), book, sources())  # each mark's own n wins
    assert [i.topic for i in book.week("A", MON)] == ["Why solar panels keep getting cheaper"]


def test_running_the_week_again_never_adds_a_second_digest():
    book = MemoryIdeaBook()
    one = [p4_item("Why solar panels keep getting cheaper")]
    run_week("A", TODAY, client(one, [judged()]), book, sources())
    again = run_week("A", TODAY, client(one, [judged()]), book, sources())
    assert again.blocked == "this week's ideas are already in the Digest"
    assert [i.rank for i in book.week("A", MON)] == [1]


def test_last_weeks_ideas_leave_even_when_this_weeks_run_stops():
    book = MemoryIdeaBook()
    old = Idea(channel="A", week=MON - dt.timedelta(days=7), rank=1, topic="How tides work")
    book.add([old])
    stopped = run_week("A", TODAY, client([p4_item("One"), p4_item("Two")], [judged()]),
                       book, sources())  # fmt: skip
    assert stopped.blocked and book.get(old.idea_id).status == IdeaStatus.EXPIRED


def test_a_held_idea_never_blocks_its_topic():
    book = MemoryIdeaBook()
    held = Idea(channel="A", week=MON - dt.timedelta(days=7), rank=0, status=IdeaStatus.HELD,
                topic="Why solar panels keep getting cheaper", held_reason="no primary source")  # fmt: skip
    book.add([held])
    run_week("A", TODAY, client([p4_item("Why solar panels keep getting cheaper")], [judged()]),
             book, sources())  # fmt: skip
    assert [i.topic for i in book.week("A", MON)] == ["Why solar panels keep getting cheaper"]


def test_a_repeat_p4_names_is_held_even_when_reworded():
    book = MemoryIdeaBook()
    items = [p4_item("Why do solar modules cost less every year",
                     is_duplicate_of="Why solar panels keep getting cheaper")]  # fmt: skip
    book.add([Idea(channel="A", week=MON - dt.timedelta(days=7), rank=1,
                   topic="Why solar panels keep getting cheaper", ratio_to_baseline=3.0)])  # fmt: skip
    run_week("A", TODAY, client(items, [judged()]), book, sources())
    (idea,) = [i for i in book.week("A", MON, shown_only=False)]
    assert idea.status == IdeaStatus.HELD and idea.held_reason == "shown in the last 90 days"
