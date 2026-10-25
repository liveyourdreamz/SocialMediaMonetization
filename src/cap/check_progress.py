"""What the full test is doing while it runs (Hemant, 25 Oct: channel A finished, then
the console sat silent for minutes and he asked "what is it waiting on?").

The pipeline saves the post after every step (on_progress=store.put); this watches the
in-memory store from a second thread and prints each new history line with the time
since the channel started, so the console always says which step is running.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from cap.store import PostStore

POLL_S = 2.0
QUIET_S = 60.0  # nothing new for this long: say it is still on the same step


@contextmanager
def narrate(store: PostStore, out: Callable[[str], None], poll_s: float = POLL_S) -> Iterator[None]:
    t0 = time.time()
    stop = threading.Event()

    def watch() -> None:
        seen, last = 0, time.time()
        while not stop.wait(poll_s):
            posts = store.all()
            lines = posts[0].history if posts else []
            for line in lines[seen:]:
                out(f"         {time.time() - t0:4.0f}s  {line[:160]}")
                last = time.time()
            seen = max(seen, len(lines))
            if time.time() - last >= QUIET_S:
                out(f"         {time.time() - t0:4.0f}s  still on that step (the model is working)")
                last = time.time()

    thread = threading.Thread(target=watch, name="full-test-progress", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=poll_s * 2)
