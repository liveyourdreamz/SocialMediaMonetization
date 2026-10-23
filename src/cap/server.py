"""The web app's composition root: the API, the screens, the guards and the error pages
put together (PRD 4.5). Entry points (cap serve, the smoke run, the offline check) call
`create_app`; nothing below this layer imports it.
"""

from __future__ import annotations

import html
import logging
import threading

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from cap.approval.app import api_router
from cap.approval.hosts import HostGuard, static_hosts
from cap.approval.identity import Reviewer, reviewer_identity, signed_in_name
from cap.approval.sessions import SessionStore
from cap.approval.throttle import SignInThrottle
from cap.audit import ChainCheck, acting_as
from cap.config import get_settings
from cap.store import MemoryStore, PostNotFoundError, PostStore
from cap.ui import STATIC, ui_router
from cap.worker import Worker

_WORKERS: dict[int, Worker] = {}  # one worker per store, keyed by the store's id
_WORKERS_GUARD = threading.Lock()
_AUDIT_CHECKS: dict[int, tuple[PostStore, ChainCheck]] = {}


def worker_for(store: PostStore) -> Worker:
    """The store's worker, started on first use (recovery first, then the loop)."""
    with _WORKERS_GUARD:
        w = _WORKERS.get(id(store))
        if w is None or w.store is not store:
            w = _WORKERS[id(store)] = Worker(store).start()
        return w


def audit_at_start(store: PostStore) -> ChainCheck:
    """Once per store: apply the retention period, then walk the chain. A broken chain
    is shown on the Operator screen and written into the log itself; nothing stops."""
    with _WORKERS_GUARD:
        done = _AUDIT_CHECKS.get(id(store))
        if done is not None and done[0] is store:
            return done[1]
    with acting_as("system"):
        removed = store.audit.purge(get_settings().audit_retention_days)
        check = store.audit.verify()
        if not check.ok:
            logging.getLogger("cap.audit").error("audit chain broken: %s", check.why)
            store.audit.append(
                "audit chain broken", detail={"at": check.broken_at, "why": check.why}
            )
        elif removed:
            store.audit.append("audit entries past retention removed", detail={"count": removed})
    with _WORKERS_GUARD:
        _AUDIT_CHECKS[id(store)] = (store, check)
    return check


def stop_workers() -> None:
    """Stop every worker this process started (the end of `cap serve`, and tests)."""
    with _WORKERS_GUARD:
        workers = list(_WORKERS.values())
        _WORKERS.clear()
        _AUDIT_CHECKS.clear()
    for w in workers:
        w.stop()


def create_app(store: PostStore | None = None, *, run_jobs: bool = True) -> FastAPI:
    """The app on `store`. With `run_jobs` (the default) the store's worker runs the
    queued jobs (briefs, final videos, the tour) in this process."""
    store = store or MemoryStore()
    settings = get_settings()
    local = settings.env in ("local", "test")
    app = FastAPI(title="Content Agent Portfolio", docs_url=None, openapi_url=None, redoc_url=None)
    app.state.store = store
    app.state.audit_check = audit_at_start(store)
    app.state.worker = worker_for(store) if run_jobs else None
    app.state.sessions = SessionStore()
    app.state.throttle = SignInThrottle()
    app.add_middleware(HostGuard, allowed=static_hosts(settings))

    @app.middleware("http")
    async def _actor(request: Request, call_next):  # type: ignore[no-untyped-def]
        """Audit entries a request writes name the person signed in (cap.audit)."""
        with acting_as(signed_in_name(request) or "anonymous"):
            return await call_next(request)

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

    @app.exception_handler(PostNotFoundError)
    async def _not_found(request: Request, exc: PostNotFoundError):  # type: ignore[no-untyped-def]
        return JSONResponse({"detail": "post not found"}, status_code=404)

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
