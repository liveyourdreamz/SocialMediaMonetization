"""`cap smoke`: the whole laptop loop, driven through the real app without a browser.

Sign in -> file a brief -> pipeline (fetch stand-in, draft, gates, video render with
the silent voice) -> queue -> blind card -> approve -> token -> Outbox preview ->
publish to a folder -> restart and find it. Mock model backend, no network. It runs
on Windows, Mac and Linux, in CI from the launchers, and on a laptop as
`uv run cap smoke` when something looks wrong. Every step prints a line; the first
failure stops it with a non-zero exit.
"""

from __future__ import annotations

import os
import shutil
import time
from collections.abc import Callable
from pathlib import Path


class SmokeError(AssertionError):
    pass


def _wait(store, topic: str, timeout: float = 60.0) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for p in store.posts.values():
            if p.brief.topic == topic and p.status.value not in ("drafting", "gated", "rendering"):
                return p.post_id
        time.sleep(0.1)
    raise SmokeError(f"brief {topic!r} did not finish within {timeout:.0f}s")


def run(root: Path, out: Callable[[str], None] = print) -> int:
    """Run the loop under `root` (wiped first). Returns 0 on success, 1 on the first failure."""
    data = root / "cap-data"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    os.environ["CAP_DATA_DIR"] = str(data)
    os.environ["CAP_MODEL_BACKEND"] = "mock"
    os.environ["CAP_RENDERER"] = "video"
    os.environ["CAP_VOICE"] = "silent"
    os.environ["CAP_PUBLISHER"] = "local"
    os.environ.setdefault("CAP_DEV_REVIEWER_HEADER", "true")
    os.environ.setdefault("CAP_ARABIC_REVIEWERS", '["layla"]')
    from fastapi.testclient import TestClient

    from cap.approval.app import FileStore, create_app
    from cap.config import get_settings
    from cap.demo.seed import seed_store
    from cap.publisher import LocalFolderAdapter
    from cap.schema.post_record import PostStatus

    get_settings.cache_clear()
    cwd = Path.cwd()
    os.chdir(root)  # the text stand-in and renders write relative paths
    steps = 0

    def step(label: str, ok: bool, detail: str = "") -> None:
        nonlocal steps
        steps += 1
        out(f"  {'ok ' if ok else 'FAIL'} {steps:2d}. {label}{(' - ' + detail) if detail else ''}")
        if not ok:
            raise SmokeError(label)

    try:
        store = FileStore(data)
        store.publisher = LocalFolderAdapter(data / "published")
        seed_store(store, demo=True, canary=True)
        api = TestClient(create_app(store), follow_redirects=False)

        def form(url: str, **d: str):
            d["csrf"] = api.cookies.get("cap_csrf") or ""
            return api.post(url, data=d)

        r = api.get("/")
        step("signed-out visit redirects to /login", r.status_code in (303, 307, 401))
        r = api.post("/login", data={"name": "hemant"})
        step("sign in as hemant", r.status_code == 303, f"status {r.status_code}")
        r = api.get("/")
        step("Portfolio renders", r.status_code == 200 and "Portfolio" in r.text)
        r = api.get("/queue")
        step("Queue renders", r.status_code == 200)
        r = api.get("/operator")
        step("Operator renders", r.status_code == 200 and "File a brief" in r.text)

        r = form("/operator/brief", channel="A", topic="Smoke test episode")
        step("file a brief for Patch", r.status_code == 303, f"status {r.status_code}")
        post_id = _wait(store, "Smoke test episode")
        rec = store.get(post_id)
        step(
            "pipeline reached the queue",
            rec.status == PostStatus.AWAITING_APPROVAL,
            f"status {rec.status.value}; last log: {rec.history[-1] if rec.history else '-'}",
        )
        step("record saved to disk", (data / "posts" / f"{post_id}.json").exists())
        video = Path(rec.asset.uri)
        step(
            "video rendered (silent voice)",
            rec.asset.kind == "video" and video.exists() and video.stat().st_size > 10_000,
            str(video),
        )
        step(
            "thumbnail rendered",
            bool(rec.asset.thumbnail_uri) and Path(rec.asset.thumbnail_uri).exists(),
        )

        r = api.get(f"/posts/{post_id}")
        step("blind card opens", r.status_code == 200 and "Approve" in r.text)
        step(
            "card is blind before the verdict",
            all(
                leak not in r.text
                for leak in ("Originality and anchor", "machines thought", "Critic")
            ),
        )
        r = form(
            f"/posts/{post_id}/verdict",
            decision="approve",
            opening_line="Smoke test opening line.",
            spot_check_supported="yes",
        )
        step("verdict recorded", r.status_code == 303, f"status {r.status_code}")
        r = form(f"/posts/{post_id}/finalise")
        step("approval finalised, token issued", r.status_code == 303 and bool(rec.approval.token))
        step(
            "post waits in the Outbox (not published yet)",
            rec.status == PostStatus.APPROVED and not (data / "published" / post_id).exists(),
        )
        r = api.get(f"/posts/{post_id}/preview")
        step(
            "Outbox preview shows the package and the player",
            r.status_code == 200 and "Publish now" in r.text and "<video" in r.text,
        )
        step("token never reaches the page", rec.approval.token not in r.text)
        r = form(f"/posts/{post_id}/publish")
        step(
            "publish verifies the token and writes the folder",
            r.status_code == 303
            and rec.status == PostStatus.PUBLISHED
            and (data / "published" / post_id / "manifest.json").exists(),
        )
        step("published video copied", (data / "published" / post_id / "long.mp4").exists())

        again = FileStore(data)
        step(
            "restart keeps the records",
            again.get(post_id).status == PostStatus.PUBLISHED
            and again.get(post_id).approval.token == rec.approval.token,
        )

        r = api.get("/digest")
        step("Digest renders", r.status_code == 200)
        r = api.get("/paused")
        step("Paused renders", r.status_code == 200)
        r = form("/tour/start")
        step("guided tour starts a tour post", r.status_code == 303)
        tour_id = r.headers["location"].split("/")[2]
        deadline = time.time() + 90
        while time.time() < deadline and store.get(tour_id).status.value in (
            "drafting",
            "gated",
            "rendering",
        ):
            time.sleep(0.2)
        tour_rec = store.get(tour_id)
        r = api.get(f"/tour/{tour_id}/9")
        step(
            "tour reaches the render stop with the video",
            r.status_code == 200
            and bool(tour_rec.asset.sha256)
            and tour_rec.asset.sha256 in r.text,
            f"status {tour_rec.status.value}",
        )
        r = form("/logout")
        step("sign out", r.status_code == 303)
        r = api.post("/login", data={"name": "layla"})
        step("sign in as layla (Arabic reviewer)", r.status_code == 303)
        r = api.get("/queue")
        step("Layla's queue renders RTL", r.status_code == 200 and 'dir="rtl"' in r.text)
        out("")
        out(f"smoke: all {steps} steps passed in {root}")
        return 0
    except SmokeError as e:
        out("")
        out(f"smoke: FAILED at: {e}")
        return 1
    finally:
        os.chdir(cwd)
