"""The code half of idea scoring (PRD 4.8, D48, D24)."""

from __future__ import annotations

import datetime as dt

import pytest

from cap.ideas.demand import DemandSignal
from cap.ideas.rank import (
    Candidate,
    build_week,
    demand_score,
    revenue_band,
    same_topic,
)
from cap.schema.ideas import Idea, IdeaStatus

MON = dt.date(2026, 10, 26)


def cand(topic, timing="evergreen", ref=None, **kw):
    base = dict(
        topic=topic, angle="real progress", demand_ref=ref, evidence_urls=("https://x",),
        timing=timing, source_gap=False, injection_suspected=False, ethics_precheck="pass",
        evidence_check="ok", constructive_fit=0.8, persona_fit=0.8, anchor_fit=0.8,
        risk_lane="low", explanation="",
    )  # fmt: skip
    base.update(kw)
    return Candidate(**base)


def sig(ref, ratio, views=10_000):
    return DemandSignal(ref, f"title {ref}", "en.wikipedia", views, ratio)


def test_held_back_never_shown_each_with_its_reason():
    week = build_week("A", MON, [
        cand("Red topic", ethics_precheck="red"),
        cand("Press release", evidence_check="promotional"),
        cand("Feel good story", evidence_check="thin"),
        cand("No source", source_gap=True),
        cand("Injected page", injection_suspected=True),
        cand("How solar cells work"),
    ], {}, [])  # fmt: skip
    shown = [i for i in week if i.status == IdeaStatus.SHOWN]
    assert [i.topic for i in shown] == ["How solar cells work"] and shown[0].rank == 1
    reasons = {i.topic: i.held_reason for i in week if i.status == IdeaStatus.HELD}
    assert reasons == {
        "Red topic": "Red lever",
        "Press release": "evidence promotional",
        "Feel good story": "evidence thin",
        "No source": "no primary source",
        "Injected page": "the page tried to instruct the reader",
    }


def test_rising_demand_ranks_higher_all_else_equal():
    week = build_week("A", MON, [cand("Flat topic", ref="d-2"), cand("Rising topic", ref="d-1")],
                      {"d-1": sig("d-1", 4.0), "d-2": sig("d-2", 1.2)}, [])  # fmt: skip
    assert [i.topic for i in week if i.rank] == ["Rising topic", "Flat topic"]
    assert week[0].views_7d == 10_000 and week[0].demand_title == "title d-1"


def test_the_mix_is_seventy_thirty_and_never_shorter_than_it_could_be():
    cands = [cand(f"Evergreen topic {n}") for n in range(20)]
    cands += [cand(f"Timely topic {n}", timing="timely", constructive_fit=0.9) for n in range(20)]
    shown = [i for i in build_week("B", MON, cands, {}, [], count=10) if i.rank]
    assert len(shown) == 10 and sum(i.timing == "evergreen" for i in shown) == 7
    few_timely = [cand(f"Evergreen topic {n}") for n in range(12)] + [
        cand("Timely one", timing="timely")
    ]
    shown = [i for i in build_week("B", MON, few_timely, {}, [], count=10) if i.rank]
    assert len(shown) == 10  # only one timely: the rest filled with evergreen
    beyond = [i for i in build_week("B", MON, cands, {}, [], count=10) if not i.rank]
    assert {i.held_reason for i in beyond} == {"below the cut"}


def test_the_ninety_day_memory_unless_demand_doubles():
    past = Idea(channel="A", week=MON - dt.timedelta(days=14), topic="How solar panels got cheap",
                ratio_to_baseline=2.0, status=IdeaStatus.EXPIRED)  # fmt: skip
    again = build_week("A", MON, [cand("Why solar panels got so cheap", ref="d-1")],
                       {"d-1": sig("d-1", 3.0)}, [past])  # fmt: skip
    assert (
        again[0].status == IdeaStatus.HELD and again[0].held_reason == "shown in the last 90 days"
    )
    doubled = build_week("A", MON, [cand("Why solar panels got so cheap", ref="d-1")],
                         {"d-1": sig("d-1", 4.5)}, [past])  # fmt: skip
    assert doubled[0].status == IdeaStatus.SHOWN


def test_same_topic_reads_meaning_words_not_filler():
    assert same_topic("How solar panels got cheap", "Why solar panels got so cheap")
    assert same_topic("How a heat pump moves heat", "How heat pumps move heat")  # plurals fold
    assert not same_topic("How solar panels got cheap", "How tides work")
    assert same_topic("كيف تعمل الألواح الشمسية", "كيف تعمل الألواح الشمسية الجديدة")


@pytest.mark.parametrize(("ratio", "score"), [(0.5, 0.0), (1.0, 0.0), (10.0, 1.0), (100.0, 1.0)])
def test_demand_scores_on_a_capped_log_scale(ratio, score):
    assert demand_score(ratio) == score


def test_revenue_is_a_band_from_the_assumption():
    assert revenue_band("A") == (10.0, 100.0)
    assert revenue_band("C") == (2.0, 20.0)
