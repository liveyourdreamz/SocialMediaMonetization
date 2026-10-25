"""The walkthrough linked from the sign-in screen (cap.ui.walkthrough). Public on
purpose and read-only: it shows drawings with example content, reads nothing from the
store and has no form, so it is safe before sign-in."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from cap.ui import walkthrough as wt
from cap.ui.page import render
from cap.ui.views.text import GATE_NAMES, REASON_CHIPS

router = APIRouter(include_in_schema=False)


@router.get("/welcome", response_class=HTMLResponse)
@router.get("/welcome/{n}", response_class=HTMLResponse)
def walkthrough(request: Request, n: int = 1) -> Response:
    try:
        step = wt.step(n)
    except IndexError:
        raise HTTPException(404, "no such step") from None
    total = len(wt.STEPS)
    return render(
        request,
        "walkthrough.html",
        None,
        n=n,
        total=total,
        s=step,
        steps=wt.STEPS,
        next_title=wt.STEPS[n].title if n < total else "",
        chips=REASON_CHIPS,
        gate_names=[name for _, name in GATE_NAMES],
        paused=False,  # the walkthrough shows no real state, signed out or not
        drafting_paused="",
    )
