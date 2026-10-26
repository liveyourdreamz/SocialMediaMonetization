"""`cap check --full`: one real post per channel, idea to a published file in a local
folder, using the real model at every step (moved out of cap.check, which kept the
seam checks). The console narrates each step as it happens (cap.check_progress)."""

from __future__ import annotations

import os
import time
import traceback
from collections.abc import Callable
from pathlib import Path


class NotPublishedError(Exception):
    """The run reached the queue but could not be approved here (says why)."""


FULL_RUN_TOPICS = {
    "A": "How a rainbow works",
    "B": "How cocoons work",
    "C": "كيف تعمل الشرانق",
}


def full_run(out: Callable[[str], None] = print, channels: str = "ABC") -> int:
    """One real brief per channel through the whole pipeline: the real model at every
    step, research, the four gates, the voice, the review video, then an approval
    through the real card functions (a stage one for channel C, the verdict with an
    opening line and the spot check, finalise), the final render with that line, and
    a publish to a local check folder. Nothing goes to your queue or to any platform.
    It reports the step and the reason for the first thing that fails in each run."""
    from cap.check_progress import narrate
    from cap.config import get_settings
    from cap.store import MemoryStore

    s = get_settings()
    if s.model_backend.value != "anthropic":
        out("full run needs your API key (the mock backend proves nothing new here)")
        return 1
    out(
        f"full run: one {s.video_length_s}s post per channel, real model, up to "
        f"{s.max_claims} claims; this takes a few minutes and uses API credit"
    )
    failed = 0
    # next to every other published copy, where Hemant can find it (not a temp folder)
    dest = (Path(s.data_dir) / "published" / "full-test").resolve()
    dest.mkdir(parents=True, exist_ok=True)
    out(f"videos go to {dest} (never to YouTube)")
    for ch in channels:
        store = MemoryStore()
        out(f"  ...  {ch} {FULL_RUN_TOPICS[ch]}: started (about 6 minutes; each step prints below)")
        with narrate(store, out):
            failed += _one_channel(store, ch, dest, out)
    out("")
    out(
        "full run passed on every channel"
        if not failed
        else f"{failed} channel(s) FAILED; the line above each says where and why"
    )
    return 1 if failed else 0


FULL_RUN_OPENING_LINE = "This is a full test run, approved by the check."


def _one_channel(store, ch: str, dest: Path, out: Callable[[str], None]) -> int:  # type: ignore[no-untyped-def]
    """One channel's run; 1 when it failed, with the line that says where and why."""
    from cap.demo.seed import run_brief
    from cap.publisher import LocalFolderAdapter
    from cap.schema.post_record import PostStatus

    topic = FULL_RUN_TOPICS[ch]
    t0 = time.time()
    try:
        rec = run_brief(store, ch, topic)
    except Exception as e:  # noqa: BLE001 - reported with the channel
        out(f"  FAIL {ch} {topic}: crashed in the pipeline: {type(e).__name__}: {e}"[:400])
        if os.environ.get("CAP_CHECK_TRACE"):
            out(traceback.format_exc())
        return 1
    took = time.time() - t0
    if rec.status != PostStatus.AWAITING_APPROVAL:
        step = rec.flags.stopped_at or rec.status.value
        out(f"  FAIL {ch} {topic}: stopped at {step}: {rec.flags.stop_reason or ''}"[:400])
        for line in rec.history[-4:]:
            out(f"         {line[:200]}")
        return 1
    try:
        _approve_and_publish(store, rec, LocalFolderAdapter(dest))
    except NotPublishedError as e:
        out(f"  skip {ch} {topic}: reached your queue in {took:.0f}s; {e}"[:400])
        return 0
    except Exception as e:  # noqa: BLE001
        out(f"  FAIL {ch} {topic}: approval or publish step: {type(e).__name__}: {e}"[:400])
        return 1
    unconfirmed = sum(1 for c in rec.draft.claims if c.status != "supported")
    out(
        f"  ok   {ch} {topic}: {time.time() - t0:.0f}s end to end · {rec.asset.duration_s:.0f}s"
        f" video{' + Short' if rec.asset.short_uri else ''} · {len(rec.draft.claims)} claims "
        f"({unconfirmed} unconfirmed) · published to {dest / rec.post_id}"
    )
    return 0


def _approve_and_publish(store, rec, adapter) -> None:  # type: ignore[no-untyped-def]
    """The real card path, in memory: stage one (channel C), the verdict with the
    opening line and the spot check, finalise, the final render, then the publisher.
    Every rule the card and the publisher enforce applies, none is set by hand."""
    from cap.approval.app import finalise_and_issue, publish_now
    from cap.config import get_settings
    from cap.domain.card import record_stage_one, record_verdict
    from cap.schema.post_record import Channel, Decision, PostStatus, ReleaseStatus
    from cap.worker import drain

    s = get_settings()
    approver = s.approvers[0]
    if rec.channel_id == Channel.C:
        if not s.arabic_reviewers:
            raise NotPublishedError(
                "not approved or published: channel C needs an Arabic reviewer "
                "(CAP_ARABIC_REVIEWERS) for its stage-one pass (D19)"
            )
        record_stage_one(
            rec, reviewer=s.arabic_reviewers[0], decision=Decision.APPROVE, notes="full run",
            time_on_card_s=rec.approval.expected_reading_s,
        )  # fmt: skip
    record_verdict(
        rec,
        Decision.APPROVE,
        reviewer=approver,
        time_on_card_s=rec.approval.expected_reading_s,
        opening_line=FULL_RUN_OPENING_LINE,
        spot_check_supported=True if rec.approval.spot_check else None,
    )
    finalise_and_issue(store, rec, approver, acknowledged=True)
    drain(store, rec.post_id, timeout=600)  # the final render, run here
    rec = store.get(rec.post_id)
    if rec.release.status != ReleaseStatus.READY:
        raise RuntimeError(f"final video: {rec.release.error or rec.release.status.value}")
    refused = publish_now(store, rec.post_id, publisher=adapter)
    rec = store.get(rec.post_id)
    if refused or rec.status != PostStatus.PUBLISHED:
        raise RuntimeError(refused or f"status after publish: {rec.status.value}")
