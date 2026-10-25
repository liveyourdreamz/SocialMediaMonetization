"""The full test says what it is doing while it runs (25 Oct: "what is it waiting on?")."""

from __future__ import annotations

import time

from cap.check_progress import narrate
from cap.demo.seed import new_brief
from cap.store import MemoryStore


def test_each_step_is_printed_as_it_happens():
    store, lines = MemoryStore(), []
    rec = new_brief("B", "How cocoons work")
    with narrate(store, lines.append, poll_s=0.01):
        rec.log("drafted the script")
        store.put(rec)
        time.sleep(0.1)
        rec.log("research: looking for sources for 8 claim(s)")
        store.put(rec)
        time.sleep(0.1)
    text = "\n".join(lines)
    assert "drafted the script" in text and "looking for sources" in text
    assert text.count("drafted the script") == 1  # each line once


def test_the_full_run_announces_each_channel_before_it_starts(monkeypatch, tmp_path):
    from cap import check
    from cap.demo import happy_fixtures
    from tests.test_check import _full_run_env

    _full_run_env(monkeypatch, tmp_path, happy_fixtures)
    lines: list[str] = []
    assert check.full_run(out=lines.append, channels="A") == 0, lines
    started = next(i for i, x in enumerate(lines) if "started" in x)
    ok = next(i for i, x in enumerate(lines) if x.strip().startswith("ok"))
    assert started < ok
