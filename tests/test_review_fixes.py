"""The 21 Oct correctness review, one test per finding that was fixed.

Each test replays the failure the review described, so a regression shows up as the
same symptom Hemant would have seen.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from cap.agents import draft
from cap.demo import CHARACTER_BLOCK, TEST_LOG
from cap.domain import token as tok
from cap.models import MockClient
from cap.pipeline import run_post
from cap.publisher import (
    DryRunAdapter,
    LocalFolderAdapter,
    PublishRefusedError,
    publish,
    publish_to,
)
from cap.schema.post_record import Channel, PostRecord, PostStatus
from tests.conftest import SOURCES, make_deps, source_docs
from tests.releases import sealed
from tests.test_research import keyed  # noqa: F401 - the fixture


def _approved(tmp_path: Path) -> tuple[PostRecord, Path, tok.ApprovalToken]:
    r, t, files = sealed(tmp_path)
    return r, files["long"], t


class _Failing:
    name = "youtube"

    def upload(self, release):
        raise RuntimeError("quota exceeded")


class _Working:
    name = "youtube"

    def upload(self, release):
        return "yt-123"


# ---- H1, H2, M7: the facts path ------------------------------------------------------


@pytest.fixture
def flagged_post(keyed, monkeypatch):  # noqa: F811 - pytest fixture injection
    """A real brief in flag mode where research finds nothing that confirms anything."""
    from cap.config import get_settings
    from cap.demo import happy_fixtures
    from cap.demo.seed import run_brief
    from cap.store import MemoryStore

    monkeypatch.setenv("CAP_RENDERER", "text")
    monkeypatch.setenv("CAP_FACT_MODE", "flag")
    get_settings.cache_clear()
    monkeypatch.setattr("cap.models.get_client", lambda fixtures=None: MockClient(happy_fixtures()))
    monkeypatch.setattr(
        "cap.research.find_sources",
        lambda topic, ch, claims=None: (
            [
                {
                    "url": "https://en.wikipedia.org/wiki/Cloud",
                    "body": "Clouds are water.",
                    "fetched_at": "",
                    "page_date": None,
                }
            ],
            ["research: one unrelated page"],
        ),
    )
    store = MemoryStore()
    rec = run_brief(store, "A", "Notely 7-day test", test_log="Day 3: sync failed twice.")
    assert rec.status == PostStatus.AWAITING_APPROVAL, rec.history[-3:]
    return store, rec


def test_h1_an_unconfirmed_claim_carries_no_quote_or_link(flagged_post):
    _, rec = flagged_post
    unconfirmed = [c for c in rec.draft.claims if c.status != "supported"]
    assert unconfirmed, "the fixture should leave at least one claim unconfirmed"
    for c in unconfirmed:
        assert c.source_quote is None and c.source_url is None, c


def test_h2_only_links_that_confirmed_a_claim_are_listed_or_published(flagged_post):
    from cap.publisher.youtube import description_for

    _, rec = flagged_post
    confirmed = {c.source_url for c in rec.draft.claims if c.status == "supported" and c.source_url}
    assert set(rec.draft.source_list) <= confirmed
    assert "wikipedia.org/wiki/Cloud" not in description_for(rec)


def test_m7_the_facts_row_is_a_near_miss_when_claims_are_unconfirmed(flagged_post):
    from cap.approval.identity import Reviewer
    from cap.ui.views import card_context

    store, rec = flagged_post
    rec.flags.gate_blind = False  # the gates as they show after the verdict (D33)
    store.put(rec)
    ctx = card_context(rec, store, Reviewer("hemant", "approver"))
    row = next(g for g in ctx["gates"] if g["name"] == "Facts and claims")
    assert row["state"] == "near" and row["line"].startswith("Pass, near miss")


# ---- L1: a failure rejects even when a park condition also holds -------------------


def test_l1_a_failed_gate_rejects_before_a_made_for_kids_park(
    fixtures, record, tmp_path, monkeypatch
):
    from cap import pipeline
    from tests.conftest import ok

    real = pipeline.check_originality

    def failing(*a, **k):
        g = real(*a, **k)
        g.passed = False
        return g

    monkeypatch.setattr(pipeline, "check_originality", failing)
    fixtures[("P9", "default")] = ok(
        factors=[{"factor_id": "child_subject_matter", "outcome": "unclear"}]
    )
    out = run_post(record, SOURCES, make_deps(MockClient(fixtures), record, tmp_path))
    assert out.status == PostStatus.REJECTED, out.history[-2:]
    assert out.flags.stopped_at == "checks" and "originality" in (out.flags.stop_reason or "")


# ---- M4: numerals the viewer reads outside the script ------------------------------


def test_m4_a_number_in_the_title_becomes_a_claim(fixtures, record):
    call1 = dict(fixtures[("P2", "1")])
    call1["draft"] = {**call1["draft"], "title": "I tested it for 42 days"}
    fixtures[("P2", "1")] = call1
    out = draft(record, MockClient(fixtures), source_docs(), CHARACTER_BLOCK, TEST_LOG)
    assert any("42" in c.text for c in out.draft.claims)


def test_m4_a_number_on_screen_becomes_a_claim(fixtures, record):
    call1 = dict(fixtures[("P2", "1")])
    call1["draft"] = {**call1["draft"], "on_screen_text": ["99% fail"]}
    fixtures[("P2", "1")] = call1
    out = draft(record, MockClient(fixtures), source_docs(), CHARACTER_BLOCK, TEST_LOG)
    assert any("99" in c.text for c in out.draft.claims)


# ---- H3: a local copy alone is not a publication -----------------------------------


def test_h3_every_platform_failing_leaves_the_post_approved(tmp_path):
    r, _, t = _approved(tmp_path)
    out = publish_to(r, t, [LocalFolderAdapter(tmp_path / "pub"), _Failing()])
    assert out.status == PostStatus.APPROVED
    assert out.publish.published_at is None and "youtube" in out.publish.errors
    assert any("not published" in h for h in out.history)
    # and the retry from the Outbox goes through once the platform works
    again = publish_to(out, t, [LocalFolderAdapter(tmp_path / "pub"), _Working()])
    assert again.status == PostStatus.PUBLISHED and again.publish.platform_post_ids["youtube"]


def test_h3_local_only_is_still_published(tmp_path):
    r, _, t = _approved(tmp_path)
    out = publish_to(r, t, [LocalFolderAdapter(tmp_path / "pub")])
    assert out.status == PostStatus.PUBLISHED


# ---- L2: a tour post never leaves the computer -------------------------------------


def test_l2_publish_refuses_a_tour_post_on_a_real_platform(tmp_path):
    r, _, t = _approved(tmp_path)
    r.flags.tour = True
    with pytest.raises(PublishRefusedError, match="guided-tour"):
        publish(r, t, _Working())
    # the local folder and the dry run are still fine
    assert publish(r, t, DryRunAdapter()).status == PostStatus.PUBLISHED


# ---- H4: a restart does not leave posts stuck in "drafting" ------------------------


@pytest.mark.parametrize(
    "status,step",
    [
        (PostStatus.BRIEFED, "draft"),
        (PostStatus.DRAFTING, "draft"),
        (PostStatus.GATED, "checks"),
        (PostStatus.RENDERING, "video"),
    ],
)
def test_h4_a_post_interrupted_by_a_restart_is_parked_with_its_step(tmp_path, status, step):
    from cap.store.files import FileStore

    first = FileStore(tmp_path)
    rec = PostRecord(channel_id=Channel.B, status=status)
    first.put(rec)
    reopened = FileStore(tmp_path)  # the app starting again
    got = reopened.get(rec.post_id)
    assert got.status == PostStatus.PARKED
    assert got.flags.stopped_at == step and "restarted" in (got.flags.stop_reason or "")
    # and it stays parked on disk, so a third start does not log it twice
    assert FileStore(tmp_path).get(rec.post_id).history == got.history


def test_h4_progress_is_saved_as_each_stage_starts(fixtures, record, tmp_path):
    seen: list[str] = []
    deps = make_deps(MockClient(fixtures), record, tmp_path)
    deps.on_progress = lambda r: seen.append(r.status.value)
    run_post(record, SOURCES, deps)
    assert seen[:3] == ["drafting", "gated", "rendering"]


# ---- M6: two saves of one post at once ---------------------------------------------


def test_m6_concurrent_saves_of_one_post_never_collide(tmp_path):
    from cap.store.files import FileStore

    store = FileStore(tmp_path)
    rec = PostRecord(channel_id=Channel.A, status=PostStatus.AWAITING_APPROVAL)
    errors: list[Exception] = []

    def save(i: int) -> None:
        try:
            for _ in range(25):
                r = rec.model_copy(deep=True)
                r.log(f"writer {i}")
                store.put(r)
        except Exception as e:  # noqa: BLE001 - the assertion below reports it
            errors.append(e)

    threads = [threading.Thread(target=save, args=(i,)) for i in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert not errors, errors
    assert not list((tmp_path / "posts").glob("*.tmp"))
    FileStore(tmp_path).get(rec.post_id)  # the file on disk is whole


# ---- R-12 follows the fact mode (22 Oct, found by the full-run test) ---------------


@pytest.mark.parametrize("mode,fires", [("flag", False), ("block", True)])
def test_r12_rejects_unsourced_claims_only_in_block_mode(monkeypatch, record, mode, fires):
    from cap.config import get_settings
    from cap.gates.ethics import hard_rules
    from cap.rulebook import load_rulebook
    from cap.schema.post_record import Claim, Platform, Variant

    monkeypatch.setenv("CAP_FACT_MODE", mode)
    get_settings.cache_clear()
    record.draft.claims = [Claim(text="Silk is protein.", status="unsupported")]
    record.draft.source_list = []
    v = Variant(platform=Platform.YOUTUBE_LONG, title="t", caption="c", script_cut="s")
    ids = [f.rule_id for f in hard_rules(record, v, load_rulebook())]
    assert ("R-12" in ids) is fires, ids
    get_settings.cache_clear()


@pytest.mark.parametrize(
    "script",
    ["مِنَ المَدارِ، هذا ما أراه.", "أنا فَلَك، قمر قديم.", "في سِجِلّي رقم واحد."],
)
def test_arabic_anchor_matches_with_or_without_vowel_marks(script):
    from cap.characters import ANCHOR_ELEMENTS
    from cap.gates import InMemoryHistory, check_originality

    rec = PostRecord(channel_id=Channel.C)
    rec.draft.title, rec.draft.script = "الشرانق", script
    g = check_originality(rec, InMemoryHistory(), ANCHOR_ELEMENTS["C"])
    assert g.anchor_present, script
