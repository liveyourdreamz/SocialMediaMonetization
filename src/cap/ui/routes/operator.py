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
from cap.jobs import JobKind
from cap.store import PostStore
from cap.ui import views
from cap.ui.page import app_store, approver_only, render

router = APIRouter(include_in_schema=False)


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
    from cap.demo.seed import new_brief

    topic = topic.strip()[:140]
    test_log = test_log[:20_000]
    if not topic:
        raise HTTPException(422, "a topic is needed")
    auto = channel == "auto"
    how = ""
    if auto:
        from cap.routing import route

        channel, how = route(topic.strip())
    elif channel not in get_settings().channels:
        raise HTTPException(422, "unknown channel")
    record = new_brief(channel, topic.strip())
    if auto:
        record.log(
            f"channel: {channel} · {get_settings().channel_characters.get(channel, '')} ({how})"
        )
    # screenshots from the test: the renderer shows them in the screen panel, in order
    saved = 0
    for i, up in enumerate(media[:10]):  # at most ten screenshots per brief
        name = Path((up.filename or "").replace("\\", "/")).name
        if not name or Path(name).suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
            continue
        data = up.file.read(15_000_001)  # never read past the limit
        if not data or len(data) > 15_000_000:
            continue
        try:  # the contents must be an image, not just the name
            import io

            from PIL import Image

            Image.open(io.BytesIO(data)).verify()
        except Exception:  # noqa: BLE001 - not an image: skipped
            continue
        folder = Path(get_settings().data_dir) / "media" / record.post_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{i:02d}-{name}").write_bytes(data)
        saved += 1
    if saved:
        record.log(f"{saved} screenshot(s) attached; they take the screen panel in order")
        record.media_manifest.real_place_or_event_depicted = False
    store.put(record)
    url_list = [u.strip() for u in urls.replace(",", "\n").splitlines() if u.strip()]
    store.jobs.enqueue(JobKind.BRIEF, record.post_id, {"urls": url_list, "test_log": test_log})
    return RedirectResponse("/operator", status_code=303)


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
    )


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
