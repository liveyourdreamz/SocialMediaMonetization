"""The screens (D38, D39), rendered server-side on the Proof design system.

Every rule the card enforces lives in cap.domain.card; these routes only collect
the reviewer's inputs and show what the card functions return. A screen can never
show what the API would refuse: the blind card is built by build_card(), which
drops the gate fields until the verdict is recorded, and card_for() drops them for
the Arabic reviewer always. Time on card is the server's clock (PostStore.open_card),
never a number the browser sends. Every POST carries a CSRF token.

The routes are in cap.ui.routes, one module per area; cap.ui.page holds what they
share.
"""

from __future__ import annotations

from fastapi import APIRouter

from cap.ui.page import STATIC, templates

__all__ = ["STATIC", "templates", "ui_router"]


def ui_router() -> APIRouter:
    from cap.ui.routes import auth, connections, operator, outbox, review, tour

    r = APIRouter(include_in_schema=False)
    for area in (auth, review, outbox, operator, connections, tour):
        r.include_router(area.router)
    return r
