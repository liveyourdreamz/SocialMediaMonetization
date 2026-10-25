"""The Operator screen and filing a brief (it queues the first job)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from cap.approval.identity import (
    Reviewer,
    check_csrf,
    current_reviewer,
)
from cap.config import get_settings
from cap.domain.cost import Spend, charge, metering
from cap.schema.post_record import PostRecord
from cap.store import PostStore
from cap.ui import views
from cap.ui.page import app_store, approver_only, render

router = APIRouter(include_in_schema=False)


IMAGE_TYPES = (".png", ".jpg", ".jpeg", ".webp")
MAX_IMAGE_BYTES = 15_000_000


@router.post("/operator/brief", dependencies=[Depends(check_csrf)])
def file_brief(
    request: Request,
    channel: str = Form(...),
    topic: str = Form(...),
    urls: str = Form(default=""),
    test_log: str = Form(default=""),
    media: list[UploadFile] = File(default=[]),
    reviewer: Reviewer = Depends(current_reviewer),
    store: PostStore = Depends(app_store),
) -> Response:
    """Brief -> draft -> critic -> adapt -> gates -> render -> queue, in the
    background (a real draft plus a rendered video takes a few minutes). The record
    appears on the Operator screen as 'drafting' at once; a fault parks it with the
    reason on the record, never a blank 500."""
    approver_only(reviewer)
    from cap.demo.canary_job import file_brief as queue_brief
    from cap.demo.seed import new_brief

    topic = topic.strip()[:140]
    if not topic:
        raise HTTPException(422, "a topic is needed")
    channel, how, spent = _channel_for(channel, topic)
    record = new_brief(channel, topic)
    charge(record, spent, get_settings().post_budget_usd)  # the routing call (R9)
    if how:
        record.log(
            f"channel: {channel} · {get_settings().channel_characters.get(channel, '')} ({how})"
        )
    _attach_screenshots(record, media)
    url_list = [u.strip() for u in urls.replace(",", "\n").splitlines() if u.strip()]
    queue_brief(store, record, {"urls": url_list, "test_log": test_log[:20_000]})
    return RedirectResponse("/operator", status_code=303)


def _channel_for(channel: str, topic: str) -> tuple[str, str, list[Spend]]:
    """(channel, how it was chosen, the model calls that chose it). "auto" asks the
    router; anything else must be a configured channel."""
    if channel != "auto":
        if channel not in get_settings().channels:
            raise HTTPException(422, "unknown channel")
        return channel, "", []
    from cap.routing import route

    with metering() as spent:
        chosen, how = route(topic)
    return chosen, how, spent


def _attach_screenshots(record: PostRecord, media: list[UploadFile]) -> None:
    """Screenshots from the test: the renderer shows them in the screen panel, in order.
    At most ten; each must really be an image and under the size limit."""
    saved = 0
    for i, up in enumerate(media[:10]):
        name = Path((up.filename or "").replace("\\", "/")).name
        data = _image_bytes(name, up)
        if data is None:
            continue
        folder = Path(get_settings().data_dir) / "media" / record.post_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{i:02d}-{name}").write_bytes(data)
        saved += 1
    if saved:
        record.log(f"{saved} screenshot(s) attached; they take the screen panel in order")
        record.media_manifest.real_place_or_event_depicted = False


def _image_bytes(name: str, up: UploadFile) -> bytes | None:
    if not name or Path(name).suffix.lower() not in IMAGE_TYPES:
        return None
    data = up.file.read(MAX_IMAGE_BYTES + 1)  # never read past the limit
    if not data or len(data) > MAX_IMAGE_BYTES:
        return None
    try:  # the contents must be an image, not just the name
        import io

        from PIL import Image

        Image.open(io.BytesIO(data)).verify()
    except Exception:  # noqa: BLE001 - not an image: skipped
        return None
    return data


@router.post("/posts/{post_id}/try-again", dependencies=[Depends(check_csrf)])
def try_again(
    post_id: str,
    reviewer: Reviewer = Depends(current_reviewer),
    store: PostStore = Depends(app_store),
) -> Response:
    """Redo the step a post stopped on after a failure (cap.demo.retry)."""
    approver_only(reviewer)
    from cap.demo.retry import TryAgainRefusedError
    from cap.demo.retry import try_again as redo

    try:
        redo(store, post_id, reviewer.name)
    except TryAgainRefusedError as e:
        raise HTTPException(409, str(e)) from None
    return RedirectResponse(f"/posts/{post_id}", status_code=303)


@router.get("/operator", response_class=HTMLResponse)
def operator(
    request: Request,
    reviewer: Reviewer = Depends(current_reviewer),
    store: PostStore = Depends(app_store),
) -> Response:
    approver_only(reviewer)
    return render(
        request,
        "operator.html",
        reviewer,
        tab="operator",
        parked=views.parked(store),
        queue_state=store,
        published=views.published(store),
        recent=views.recent_briefs(store),
        outbox=views.outbox(store),
        connections_summary=_connections_summary(),
        build=__import__("cap.version", fromlist=["build_id"]).build_id(),
        running=any(p.status.value in ("drafting", "gated", "rendering") for p in store.all()),
        audit_check=getattr(request.app.state, "audit_check", None),
        idea_runs=_idea_runs(store),
        alerts=_alerts(store),
    )


def _alerts(store: PostStore, n: int = 8) -> list[str]:
    """The last alerts, newest first, from the audit log."""
    found = [e for e in store.audit.entries() if e.action == "alert"][-n:]
    return [f"{e.at:%a %d %b %H:%M} · {e.detail.get('subject', '')}: {e.detail.get('body', '')}"
            for e in reversed(found)]  # fmt: skip


def _idea_runs(store: PostStore, n: int = 6) -> list[str]:
    """The last weekly trend-research reports, newest first, from the audit log."""
    runs = [e for e in store.audit.entries() if e.action in ("ideas run", "ideas run failed")]
    lines = []
    for e in reversed(runs[-n:]):
        text = e.detail.get("report") or f"{e.detail.get('job', '')}: {e.detail.get('why', '')}"
        lines.append(f"{e.at:%a %d %b %H:%M} · {text}")
    return lines


def _connections_summary() -> str:
    from cap import connections as conn

    names = [
        conn.status(k)["account"].get("name", k)
        for k in conn.PLATFORMS
        if conn.status(k)["connected"]
    ]
    return ("connected: " + ", ".join(names)) if names else "none connected yet"
