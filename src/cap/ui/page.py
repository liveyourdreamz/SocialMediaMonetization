"""What every screen shares: the templates, the page context, the store dependency
and the small input helpers. The routes live in cap.ui.routes, one module per area."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from cap.approval.deps import app_store
from cap.approval.identity import Reviewer, csrf_token
from cap.config import get_settings
from cap.domain.queue import drafting_pause
from cap.ui import views

HERE = Path(__file__).resolve().parent
STATIC = StaticFiles(directory=str(HERE / "static"))
templates = Jinja2Templates(directory=str(HERE / "templates"))
templates.env.filters["minutes"] = views.minutes
templates.env.filters["day"] = views.day
templates.env.globals["workflow"] = views.workflow


def render(
    request: Request, template: str, reviewer: Reviewer | None, status: int = 200, **ctx: Any
) -> Response:
    store = app_store(request)
    approver = bool(reviewer and reviewer.is_approver)
    queue = store.queue() if approver else []
    page = {
        "request": request,
        "reviewer": reviewer,
        "paused": store.paused,
        "queue_count": len(queue),
        "drafting_paused": drafting_pause(
            queue, datetime.now(UTC), get_settings().queue_idle_pause_hours
        ),
        "settings": get_settings(),
        "csrf": csrf_token(request),
        **ctx,
    }
    return templates.TemplateResponse(request, template, page, status_code=status)


def approver_only(reviewer: Reviewer) -> None:
    if not reviewer.is_approver:
        raise HTTPException(403, "approvers only")


def enum_value(kind: type, value: str | None, what: str) -> Any:
    if value in (None, ""):
        return None
    try:
        return kind(value)
    except ValueError as e:
        raise HTTPException(422, f"unknown {what}: {value!r}") from e


def spot(value: str | None) -> bool | None:
    return None if value in (None, "") else value == "yes"
