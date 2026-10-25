"""Where a post is in the workflow, and what happens next (the progress bar on every page)."""

from __future__ import annotations

from typing import Any

from cap.demo.retry import can_try_again
from cap.schema.post_record import PostRecord
from cap.ui.views.outbox import release_view

# ---- the workflow: where a post is, what is left, what to do next ---------------
STEPS: list[tuple[str, str]] = [
    ("brief", "Brief"),
    ("draft", "Draft"),
    ("research", "Sources"),
    ("checks", "Checks"),
    ("video", "Video"),
    ("review", "Your review"),
    ("publish", "Publish"),
]

_RUNNING = ("drafting", "gated", "rendering")


def _stopped_at(record: PostRecord) -> str:
    """For a parked or rejected post: the step whose work did not finish."""
    last = " ".join(record.history[-3:]).lower()
    if "brief needs" in last:
        return "brief"
    if any(k in last for k in ("render", "voice", "ffmpeg")):
        return "video"
    if any(k in last for k in ("gate", "ethics", "made-for-kids", "injection", "p10", "facts")):
        return "checks"
    if "research" in last or "fetch" in last:
        return "research"
    if not record.draft.script or any(k in last for k in ("p2", "p3", "p7", "draft")):
        return "draft"
    return "checks"


# the step a live post is on, by status (gated splits into research, then checks)
_CURRENT = {
    "drafting": "draft",
    "rendering": "video",
    "awaiting_approval": "review",
    "approved": "publish",
}
_DOING = {
    "brief": "Brief filed",
    "draft": "Writing the script",
    "research": "Finding sources for each claim",
    "checks": "Running the four checks",
    "video": "Rendering voice and video",
}
_WAIT = "Nothing to do yet; this page updates itself"


def _position(record: PostRecord) -> tuple[str, str]:
    """(current step, stopped step); both empty once published."""
    st = record.status.value
    if st == "published":
        return "", ""
    if st in ("parked", "rejected", "discarded"):
        stopped = record.flags.stopped_at or _stopped_at(record)
        return stopped, stopped
    if st == "gated":
        hist = " ".join(record.history).lower()
        return ("checks" if "research:" in hist or "source:" in hist else "research"), ""
    return _CURRENT.get(st, "brief"), ""


def _states(current: str, stopped: str) -> tuple[list[dict[str, str]], int]:
    keys = [k for k, _ in STEPS]
    idx = keys.index(current) if current else len(keys)
    steps = []
    for i, (k, label) in enumerate(STEPS):
        state = (
            "stopped"
            if k == stopped
            else ("done" if i < idx else "current" if i == idx else "todo")
        )
        steps.append({"key": k, "label": label, "state": state})
    return steps, idx


def _reason(record: PostRecord) -> str:
    if record.flags.stop_reason:
        return record.flags.stop_reason
    # records from before stop_reason existed
    last = next(
        (h for h in reversed(record.history) if "parked: " in h or "gates failed: " in h),
        record.history[-1] if record.history else "",
    )
    return last.split(" ", 1)[-1].removeprefix("parked: ")


def _publish_action(record: PostRecord) -> tuple[str, str]:
    rel = release_view(record)
    if rel["status"] == "preparing":
        return "Preparing the final video", _WAIT
    if rel["status"] in ("failed", "none"):
        return rel["text"], "See why and make it again"
    if rel["can_reissue"]:
        return "Approved; the token needs re-issuing", "Re-issue the token"
    return "Approved, waiting to publish", "Choose where it goes and publish"


def _action(record: PostRecord, current: str, stopped: str) -> tuple[str, str, str]:
    """(headline, next action, where it is done)."""
    pid = record.post_id
    if record.status.value == "published":
        return "Published", "See where it went", f"/posts/{pid}/preview#where"
    if stopped == "review":
        return "Closed at your review", "See the card", f"/posts/{pid}"
    if stopped:
        label = dict(STEPS)[stopped]
        return (
            f"Stopped at {label.lower()}",
            "File the brief again once the cause is fixed",
            "/operator#brief",
        )
    if current == "review":
        if record.approval.pre_reveal_decision is None:
            return "Ready for your review", "Read it, then approve or reject", f"/posts/{pid}"
        return "Verdict recorded", "Keep or change your verdict", f"/posts/{pid}"
    if current == "publish":
        headline, nxt = _publish_action(record)
        return headline, nxt, f"/posts/{pid}/preview#publish"
    return _DOING[current], _WAIT, ""


def _restart(record: PostRecord, stopped: str, stuck: bool) -> dict[str, str] | None:
    """The Restart button on a failed or hung step: its label, why, and where it posts."""
    if not stopped or not (stuck or can_try_again(record)):
        return None
    step = dict(STEPS)[stopped].lower()
    if stuck:
        why = (f"The {step} step has run far longer than it should; it may have lost its "
               "connection. Restarting runs it again with the same inputs.")  # fmt: skip
    else:
        cause = _reason(record).split(" could not run: ", 1)[-1].removeprefix("interrupted: ")
        why = (f"The {step} step failed ({cause}). Once the cause is gone, restarting runs "
               "it again with the same inputs.")  # fmt: skip
    return {"label": f"Restart the {step} step", "reason": why,
            "url": f"/posts/{record.post_id}/restart"}  # fmt: skip


def workflow(record: PostRecord, stuck: bool = False) -> dict[str, Any]:
    """The seven steps of one post with a state each (done, current, stopped, todo), the
    step number, a one-line status and the next action: the same everywhere a post is
    shown, so 'where am I and what is left' always has one answer. A step that failed,
    or hangs (`stuck`: running past cap.demo.retry.STUCK_AFTER), carries a Restart
    button against it."""
    st = record.status.value
    current, stopped = _position(record)
    if stuck and current and not stopped:
        stopped = current  # shown as a problem, not as working
    steps, idx = _states(current, stopped)
    headline, nxt, href = _action(record, current, stopped)
    restart = _restart(record, stopped, stuck)
    if restart:  # the button sits on the step; nothing else to do
        label = dict(STEPS)[stopped].lower()
        headline, nxt, href = (f"Stuck at {label}" if stuck else f"Stopped at {label}"), "", ""
    return {
        "steps": steps,
        "number": min(idx + 1, len(STEPS)) if current or stopped else len(STEPS),
        "total": len(STEPS),
        "done": sum(1 for s in steps if s["state"] == "done"),
        "headline": headline,
        "reason": restart["reason"] if restart else (_reason(record) if stopped else ""),
        "restart": restart,
        "next": nxt,
        "href": href,
        "running": not stuck
        and (st in _RUNNING or (st == "approved" and record.release.status.value == "preparing")),
        "stopped": bool(stopped),
        "finished": st == "published",
    }
