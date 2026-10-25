"""The walkthrough from the sign-in screen (Hemant, 25 Oct): every step opens without
signing in, shows no real state, and quotes the screens' own words, so it cannot drift
from what he will actually see."""

from __future__ import annotations

from pathlib import Path

import pytest

from cap.server import create_app
from cap.store import MemoryStore
from cap.ui import walkthrough as wt
from tests.test_ui import Browser

TEMPLATES = Path(__file__).parents[1] / "src" / "cap" / "ui" / "templates"


@pytest.fixture
def anon():
    store = MemoryStore()
    return store, Browser(create_app(store), follow_redirects=False)


def test_the_sign_in_screen_offers_the_walkthrough(anon):
    _, b = anon
    page = b.get("/login").text
    assert 'href="/welcome"' in page and "Show me around" in page


def test_every_step_opens_signed_out_and_leads_to_the_next(anon):
    _, b = anon
    total = len(wt.STEPS)
    for n, step in enumerate(wt.STEPS, start=1):
        r = b.get(f"/welcome/{n}")
        assert r.status_code == 200, (n, r.status_code)
        page = r.text
        assert step.title in page and f"Step {n} of {total}" in page
        assert all(d.replace("…", "") in page.replace("…", "") for d in step.do)
        if n < total:
            assert f'href="/welcome/{n + 1}"' in page
        else:
            assert "Sign in and start" in page
    assert b.get("/welcome").status_code == 200
    assert b.get(f"/welcome/{total + 1}").status_code == 404
    assert b.get("/welcome/0").status_code == 404


def test_the_walkthrough_shows_no_real_state(anon):
    store, b = anon
    store.pause("a canary was missed")
    page = b.get("/welcome/1").text
    assert "Queue paused" not in page  # the real band is never shown here
    assert "<form" not in page  # nothing to submit: it changes nothing


@pytest.mark.parametrize(
    ("words", "template"),
    [
        ("Pick", "digest.html"),
        ("Not this one", "digest.html"),
        ("Create the post", "operator.html"),
        ("Reject with this reason", "card.html"),
        ("Escalate", "card.html"),
        ("Approve…", "card.html"),
        ("Keep approve", "card.html"),
        ("Change to reject", "card.html"),
        ("Now, what the machines thought", "card.html"),
        ("Publish now", "preview.html"),
        ("Outbox: approved, waiting to publish", "operator.html"),
        ("Yes, it does", "approve.html"),
        ("Write the opening line", "approve.html"),
        ("Parked posts", "operator.html"),
        ("Alerts", "operator.html"),
        (">Portfolio<", "base.html"),
        (">Queue", "base.html"),
        (">Digest<", "base.html"),
        (">Operator<", "base.html"),
    ],
)
def test_the_words_it_quotes_are_on_the_real_screens(words, template):
    assert words.strip("<>") in (TEMPLATES / "walkthrough.html").read_text(
        encoding="utf-8"
    ) + " ".join(s.what + " ".join(s.do) + s.where + s.tip for s in wt.STEPS)
    assert words in (TEMPLATES / template).read_text(encoding="utf-8"), (words, template)
