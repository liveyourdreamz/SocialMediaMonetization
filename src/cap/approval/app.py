"""The approval app (PRD 4.5, D14, D39): the HTML screens the reviewers use and the
JSON API underneath them.

HTML (cap.ui): /  Portfolio · /queue · /posts/{id} · /posts/{id}/approve · /paused ·
/digest · /operator · /login (local stand-in for Google sign-in).

JSON, under /api, same rules, same store:
  GET  /api/queue                 cards awaiting approval (blind view)
  GET  /api/posts/{id}/card       one card
  POST /api/posts/{id}/stage-one  Arabic reviewer's pass (channel C, D19)
  POST /api/posts/{id}/verdict    first verdict (D33 pre-reveal) -> revealed gates
  POST /api/posts/{id}/finalise   optional single revision; on approve the final render
                                  starts, and the token is issued when it is done
  GET  /api/posts/{id}/release    the final release: status, error, token present

Identity: cap.approval.identity (a server-held session; the X-Reviewer header only
under the test-only setting). Host headers: cap.approval.hosts.
"""

from __future__ import annotations

import html
import json
import logging
import os
import threading
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from cap.approval import release_job
from cap.approval.card import (
    CanaryMissedError,
    CardError,
    CardView,
    build_card,
    finalise,
    record_stage_one,
    record_verdict,
)
from cap.approval.hosts import HostGuard, static_hosts
from cap.approval.identity import Reviewer, reviewer_identity, signed_in_name
from cap.approval.sessions import SessionStore
from cap.approval.throttle import SignInThrottle
from cap.config import get_settings
from cap.publisher import PlatformAdapter, PublishRefusedError
from cap.schema.migrate import load_record
from cap.schema.post_record import (
    Channel,
    Decision,
    PostRecord,
    PostStatus,
    ReasonCode,
    ReleaseStatus,
)


class Store:
    """In-memory store for Foundation; the Cloud SQL repository replaces it with the
    same methods. `opened_at` is the server's clock on a card (D34: time on card is
    measured here, never trusted from the browser). `analytics` is what agent 14
    fills; empty until it runs, and the Portfolio says so."""

    def __init__(self) -> None:
        self.posts: dict[str, PostRecord] = {}
        self.paused: bool = False  # P14: a missed canary pauses the session
        self.paused_reason: str = ""
        self.opened_at: dict[tuple[str, str], datetime] = {}
        self.analytics: dict[str, Any] = {}  # agent 14's figures, with source and pull time
        self.analytics_note: str = ""  # "example figures" in demo mode; else empty
        self.publisher: PlatformAdapter | None = None  # set by the app; None = approve only

    def local_published_dir(self) -> Path:
        """Where the local record of every publish goes (cap-data/published)."""
        from cap.config import get_settings

        return Path(get_settings().data_dir) / "published"

    def get(self, post_id: str) -> PostRecord:
        try:
            return self.posts[post_id]
        except KeyError as e:
            raise HTTPException(404, "post not found") from e

    def put(self, record: PostRecord) -> None:
        self.posts[record.post_id] = record

    def queue(self) -> list[PostRecord]:
        waiting = [p for p in self.posts.values() if p.status == PostStatus.AWAITING_APPROVAL]
        return sorted(waiting, key=lambda p: p.created_at)  # oldest first, the only priority

    def open_card(self, post_id: str, reviewer: str) -> int:
        """First open starts the clock; later opens return seconds elapsed so far."""
        key = (post_id, reviewer)
        now = datetime.now(UTC)
        if key not in self.opened_at:
            self.opened_at[key] = now
            return 0
        return int((now - self.opened_at[key]).total_seconds())

    def seconds_on_card(self, post_id: str, reviewer: str) -> int:
        at = self.opened_at.get((post_id, reviewer))
        return int((datetime.now(UTC) - at).total_seconds()) if at else 0

    def pause(self, reason: str) -> None:
        self.paused, self.paused_reason = True, reason
        self.save_state()

    def reopen(self) -> None:
        self.paused, self.paused_reason = False, ""
        self.save_state()

    def save_state(self) -> None:  # the file-backed store overrides
        return None


class FileStore(Store):
    """The laptop build's store: one JSON file per post under <data_dir>/posts and a
    state file for the paused flag and analytics. Writes are atomic (write, then
    rename). The Cloud SQL repository replaces this with the same methods."""

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = root
        posts = self.root / "posts"
        posts.mkdir(parents=True, exist_ok=True)
        self.quarantined: list[Path] = []
        # Each file on its own (review H8): one record the build cannot read is moved to
        # posts/quarantine with the reason beside it, and start-up carries on.
        for f in sorted(posts.glob("*.json")):
            try:
                loaded = load_record(f.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:  # bad JSON, bad UTF-8, failed validation
                self.quarantined.append(_quarantine(f, posts / "quarantine", e))
                continue
            rec = loaded.record
            if loaded.dropped:
                _log.info(
                    "%s: set aside fields this build does not know: %s", f.name, loaded.dropped
                )
            if rec.status in _IN_FLIGHT:
                # the thread that was working on it died with the app (review H4)
                step = _IN_FLIGHT[rec.status]
                rec.stop(step, "interrupted: the app restarted while this was running")
                rec.status = PostStatus.PARKED
                rec.log("parked: interrupted by a restart; file it again")
                _atomic_write(f, rec.model_dump_json(indent=1))
            self.posts[rec.post_id] = rec
        state = self.root / "state.json"
        if state.exists():
            try:
                d = json.loads(state.read_text(encoding="utf-8"))
                if not isinstance(d, dict):
                    raise ValueError("state.json is not a JSON object")
            except (OSError, ValueError) as e:
                self.quarantined.append(_quarantine(state, self.root / "quarantine", e))
                d = {}
            self.paused = bool(d.get("paused"))
            self.paused_reason = d.get("paused_reason", "")
            self.analytics = d.get("analytics", {})
            self.analytics_note = d.get("analytics_note", "")

    def put(self, record: PostRecord) -> None:
        super().put(record)
        _atomic_write(
            self.root / "posts" / f"{record.post_id}.json", record.model_dump_json(indent=1)
        )

    def save_state(self) -> None:
        _atomic_write(
            self.root / "state.json",
            json.dumps(
                {
                    "paused": self.paused,
                    "paused_reason": self.paused_reason,
                    "analytics": self.analytics,
                    "analytics_note": self.analytics_note,
                },
                indent=1,
            ),
        )


_log = logging.getLogger("cap.store")


def _quarantine(path: Path, folder: Path, error: Exception) -> Path:
    """Move an unreadable record aside with a sidecar <name>.error.txt saying why, and
    log one line. Nothing is deleted: the file can be fixed and moved back."""
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / path.name
    if dest.exists():
        dest = folder / f"{path.stem}.{datetime.now(UTC):%Y%m%dT%H%M%S}{path.suffix}"
    os.replace(path, dest)
    dest.with_name(dest.name + ".error.txt").write_text(
        f"{datetime.now(UTC).isoformat()} moved here from {path.parent.name}/ at start-up:\n"
        f"this build could not read it ({type(error).__name__}).\n\n{error}\n",
        encoding="utf-8",
    )
    _log.warning("could not read %s; moved it to %s (reason beside it)", path.name, dest)
    return dest


_IN_FLIGHT = {
    PostStatus.BRIEFED: "draft",
    PostStatus.DRAFTING: "draft",
    PostStatus.GATED: "checks",
    PostStatus.RENDERING: "video",
}


_WRITE_LOCKS: dict[Path, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _atomic_write(path: Path, text: str) -> None:
    """Write, then rename. One lock per file: two threads saving the same post take
    turns (on Windows a rename onto a file another rename is replacing is refused,
    review M6), and each uses its own temp name."""
    import time
    import uuid

    with _LOCKS_GUARD:
        lock = _WRITE_LOCKS.setdefault(path.resolve(), threading.Lock())
    with lock:
        tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")
        tmp.write_text(text, encoding="utf-8")
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:  # Windows: a reader (or antivirus) holds it briefly
                if attempt == 4:
                    tmp.unlink(missing_ok=True)
                    raise
                time.sleep(0.05 * (attempt + 1))


class StageOneIn(BaseModel):
    decision: Decision
    notes: str = ""


class VerdictIn(BaseModel):
    decision: Decision
    reason_code: ReasonCode | None = None
    opening_line: str | None = None
    spot_check_supported: bool | None = None


class FinaliseIn(BaseModel):
    revised_decision: Decision | None = None
    reason_code: ReasonCode | None = None
    opening_line: str | None = None
    spot_check_supported: bool | None = None
    acknowledged: bool = False


def finalise_and_issue(store: Store, record: PostRecord, reviewer: str, **kw: Any) -> bool:
    """Shared by the API and the HTML app: finalise, pause on a missed canary, and on
    approve start the final render (cap.approval.release_job), which issues the token
    once the final files exist (review C1). Returns whether the final render started.
    Raises CardError."""
    try:
        finalise(record, reviewer=reviewer, **kw)
    except CanaryMissedError:
        store.pause(f"canary missed on {record.post_id} by {reviewer}")
        store.put(record)
        return False
    store.put(record)
    if record.status != PostStatus.APPROVED:
        return False
    try:
        release_job.start(store, record)
    except PublishRefusedError as e:  # the approval does not hold for release: say so
        record.release.status = ReleaseStatus.FAILED
        record.release.error = str(e)
        record.log(f"final video not started: {e}")
        store.put(record)
        return False
    return True


def publish_now(
    store: Store, record: PostRecord, destinations: list[str] | None = None
) -> str | None:
    """The plain-code publisher (PRD 4.6) on one approved post; cap.release.authorise
    decides, before every upload, whether it may go. Returns the refusal text, or
    None when the publisher ran (each destination's result is on the record).
    `destinations` are the Outbox's choices (youtube_long, facebook, ...); the local
    folder is always first. A tour post never leaves the laptop. The scheduler calls
    this at the posting slot; the Outbox button calls it now."""
    from cap.publisher import adapters_for, publish_to
    from cap.release import decode_token

    chosen = [] if record.flags.tour else list(destinations or [])
    if store.publisher is not None and destinations is None and not record.flags.tour:
        # a programmatic call (the scheduler, a test) uses the configured publisher;
        # the Outbox always passes a list, so the ticked boxes decide there
        adapters: list[PlatformAdapter] = [store.publisher]
        wanted = [store.publisher.name]
    else:
        adapters = adapters_for(chosen, store.local_published_dir())
        wanted = ["local", *chosen]
    try:
        record.publish.destinations = wanted
        publish_to(record, decode_token(record), adapters, save=store.put)
    except PublishRefusedError as e:
        record.log(f"publish refused: {e}")
        store.put(record)
        return str(e)
    return None


def visible_to(record: PostRecord, reviewer: Reviewer) -> bool:
    """D19: the Arabic reviewer sees channel C only; approvers see everything."""
    return reviewer.is_approver or record.channel_id == Channel.C


def card_for(record: PostRecord, reviewer: Reviewer) -> CardView:
    """The card as this reviewer may see it. The Arabic reviewer never sees gate
    outcomes, critic notes or planted faults, whatever the record's state (D19)."""
    if not visible_to(record, reviewer):
        raise HTTPException(403, "channel C only")
    view = build_card(record)
    if not reviewer.is_approver:
        view.gates, view.critic_issues, view.planted_fault, view.gates_revealed = (
            None,
            None,
            None,
            False,
        )
    return view


def api_router(store: Store) -> APIRouter:
    r = APIRouter(prefix="/api")

    def _guard_paused() -> None:
        if store.paused:
            raise HTTPException(423, "session paused after a missed canary; operator review needed")

    def _approver(reviewer: Reviewer) -> None:
        if not reviewer.is_approver:
            raise HTTPException(403, "approvers only")

    @r.get("/queue")
    def queue(reviewer: Reviewer = Depends(reviewer_identity)) -> list[dict[str, Any]]:
        return [asdict(card_for(p, reviewer)) for p in store.queue() if visible_to(p, reviewer)]

    @r.get("/posts/{post_id}/card")
    def card(post_id: str, reviewer: Reviewer = Depends(reviewer_identity)) -> dict[str, Any]:
        record = store.get(post_id)
        view = card_for(record, reviewer)
        if record.status == PostStatus.AWAITING_APPROVAL:
            store.open_card(post_id, reviewer.name)  # the clock starts here, server-side
        return asdict(view)

    @r.post("/posts/{post_id}/stage-one")
    def stage_one(
        post_id: str, body: StageOneIn, reviewer: Reviewer = Depends(reviewer_identity)
    ) -> dict[str, Any]:
        _guard_paused()
        if reviewer.role != "arabic":
            raise HTTPException(403, "the Arabic reviewer records stage one")
        record = store.get(post_id)
        try:
            record_stage_one(
                record,
                reviewer=reviewer.name,
                decision=body.decision,
                notes=body.notes,
                time_on_card_s=store.seconds_on_card(post_id, reviewer.name),
            )
        except CardError as e:
            raise HTTPException(422, str(e)) from e
        store.put(record)
        return {"post_id": record.post_id, "stage_one": body.decision.value}

    @r.post("/posts/{post_id}/verdict")
    def verdict(
        post_id: str, body: VerdictIn, reviewer: Reviewer = Depends(reviewer_identity)
    ) -> dict[str, Any]:
        _guard_paused()
        _approver(reviewer)
        record = store.get(post_id)
        try:
            record_verdict(
                record,
                body.decision,
                reviewer=reviewer.name,
                time_on_card_s=store.seconds_on_card(post_id, reviewer.name),
                reason_code=body.reason_code,
                opening_line=body.opening_line,
                spot_check_supported=body.spot_check_supported,
            )
        except CardError as e:
            raise HTTPException(422, str(e)) from e
        store.put(record)
        return asdict(build_card(record))  # gates now revealed (D33)

    @r.post("/posts/{post_id}/finalise")
    def finalise_post(
        post_id: str, body: FinaliseIn, reviewer: Reviewer = Depends(reviewer_identity)
    ) -> dict[str, Any]:
        _guard_paused()
        _approver(reviewer)
        record = store.get(post_id)
        try:
            started = finalise_and_issue(
                store,
                record,
                reviewer.name,
                revised_decision=body.revised_decision,
                reason_code=body.reason_code,
                opening_line=body.opening_line,
                spot_check_supported=body.spot_check_supported,
                acknowledged=body.acknowledged,
            )
        except CardError as e:
            raise HTTPException(422, str(e)) from e
        if store.paused and record.flags.canary_outcome == "missed":
            return {
                "post_id": record.post_id,
                "status": record.status.value,
                "canary": "missed",
                "session_paused": True,
            }
        out: dict[str, Any] = {
            "post_id": record.post_id,
            "status": record.status.value,
            # the final render runs in the background; GET .../release says when the
            # token is there
            "final_render_started": started,
        }
        if record.flags.canary:
            out["canary"] = record.flags.canary_outcome
        return out

    @r.get("/posts/{post_id}/release")
    def release_state(
        post_id: str, reviewer: Reviewer = Depends(reviewer_identity)
    ) -> dict[str, Any]:
        _approver(reviewer)
        record = store.get(post_id)
        return {
            "post_id": record.post_id,
            "status": record.status.value,
            "release": record.release.status.value,
            "error": record.release.error,
            "token_issued": bool(record.approval.token),
        }

    return r


def create_app(store: Store | None = None) -> FastAPI:
    from cap.ui import (
        STATIC,
        ui_router,
    )  # the screens; imported here to keep the API importable alone

    store = store or Store()
    settings = get_settings()
    local = settings.env in ("local", "test")
    app = FastAPI(title="Content Agent Portfolio", docs_url=None, openapi_url=None, redoc_url=None)
    app.state.store = store
    app.state.sessions = SessionStore()
    app.state.throttle = SignInThrottle()
    app.add_middleware(HostGuard, allowed=static_hosts(settings))
    app.include_router(api_router(store))
    app.include_router(ui_router(store))
    app.mount("/static", STATIC, name="static")
    if local:
        _api_docs(app)

    @app.exception_handler(Exception)
    async def _crash(request: Request, exc: Exception):  # type: ignore[no-untyped-def]
        """A fault on a laptop run tells a signed-in reviewer what broke (type, message,
        file and line) so it can be reported; anyone else, and every run outside
        local, gets a plain 500 with the detail in the log."""
        import logging
        import traceback

        logging.getLogger("cap").exception("unhandled error on %s", request.url.path)
        if not local or signed_in_name(request) is None:
            return JSONResponse({"detail": "internal error"}, status_code=500)
        tb = traceback.extract_tb(exc.__traceback__)
        where = f"{tb[-1].filename.split('cap')[-1]}:{tb[-1].lineno}" if tb else "?"
        body = (
            "<!doctype html><meta charset='utf-8'><title>Something broke</title>"
            "<body style='font: 15px/1.5 system-ui; max-width: 640px; margin: 48px auto; padding: 0 16px'>"
            "<h1 style='font-size: 22px'>Something broke on this page</h1>"
            f"<p><b>{type(exc).__name__}</b>: {html.escape(str(exc)[:500])}</p>"
            f"<p style='color:#5c5955'>at cap{html.escape(where)} · {request.method} "
            f"{html.escape(request.url.path)}</p>"
            "<p>Copy this page's text into the chat and it gets fixed. Nothing was published.</p>"
            "<p><a href='/'>Portfolio</a> · <a href='/queue'>Queue</a></p></body>"
        )
        from fastapi.responses import HTMLResponse

        return HTMLResponse(body, status_code=500)

    @app.exception_handler(HTTPException)
    async def _html_aware(request: Request, exc: HTTPException):  # type: ignore[no-untyped-def]
        """The screens get a sign-in redirect, not a JSON body, when the cookie has
        expired; the API keeps JSON."""
        if exc.status_code == 401 and not request.url.path.startswith("/api"):
            return RedirectResponse("/login", status_code=303)
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    return app


def _api_docs(app: FastAPI) -> None:
    """The API's docs, on a laptop run only, to a signed-in approver only."""
    from fastapi.openapi.docs import get_swagger_ui_html
    from fastapi.responses import HTMLResponse

    def _approver(reviewer: Reviewer = Depends(reviewer_identity)) -> None:
        if not reviewer.is_approver:
            raise HTTPException(403, "approvers only")

    @app.get("/api/openapi.json", include_in_schema=False, dependencies=[Depends(_approver)])
    def openapi() -> JSONResponse:
        return JSONResponse(app.openapi())

    @app.get("/api/docs", include_in_schema=False, dependencies=[Depends(_approver)])
    def docs() -> HTMLResponse:
        return get_swagger_ui_html(openapi_url="/api/openapi.json", title=app.title)
