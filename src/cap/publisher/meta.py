"""Meta adapter: a Reel on the Facebook Page (resumable upload of the file) and, when
the video is reachable at a public URL, a Reel on the linked Instagram account.

Instagram's publishing API only accepts a URL it can fetch, never an upload, so on a
laptop (no public address) the Instagram step is skipped and says so; set
CAP_PUBLIC_BASE_URL when the rendered files are served from a public host.

Not yet run against a live Page by us: the Connections screen says so. Graph API
v21.0; the page token comes from the connection store.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import httpx

from cap import connections
from cap.publisher.youtube import description_for
from cap.schema.post_record import PostRecord

GRAPH = "https://graph.facebook.com/v21.0"


class MetaAdapter:
    name = "meta"

    def __init__(self) -> None:
        data = connections.load("meta")
        acct = data.get("account") or {}
        self.page_id = acct.get("id", "")
        self.page_token = acct.get("page_token", "")
        self.ig_id = acct.get("instagram_id", "")
        if not self.page_id or not self.page_token:
            raise RuntimeError("Facebook is not connected; open Connections in the app")

    def _facebook_reel(self, record: PostRecord, video: Path) -> str:
        with httpx.Client(timeout=120) as c:
            r = c.post(
                f"{GRAPH}/{self.page_id}/video_reels",
                data={"upload_phase": "start", "access_token": self.page_token},
            )
            r.raise_for_status()
            vid = r.json()["video_id"]
            size = video.stat().st_size
            with video.open("rb") as f:
                r = c.post(
                    f"https://rupload.facebook.com/video-reels/{vid}",
                    headers={
                        "Authorization": f"OAuth {self.page_token}",
                        "offset": "0",
                        "file_size": str(size),
                        "Content-Type": "application/octet-stream",
                    },
                    content=f.read(),
                )
            r.raise_for_status()
            r = c.post(
                f"{GRAPH}/{self.page_id}/video_reels",
                data={
                    "upload_phase": "finish",
                    "video_id": vid,
                    "video_state": "PUBLISHED",
                    "description": description_for(record)[:2000],
                    "access_token": self.page_token,
                },
            )
            r.raise_for_status()
        return str(vid)

    def _instagram_reel(self, record: PostRecord, public_url: str) -> str:
        with httpx.Client(timeout=120) as c:
            r = c.post(
                f"{GRAPH}/{self.ig_id}/media",
                data={
                    "media_type": "REELS",
                    "video_url": public_url,
                    "caption": description_for(record)[:2200],
                    "access_token": self.page_token,
                },
            )
            r.raise_for_status()
            container = r.json()["id"]
            for _ in range(60):  # Instagram processes the video before it can be published
                s = c.get(
                    f"{GRAPH}/{container}",
                    params={"fields": "status_code", "access_token": self.page_token},
                )
                if s.json().get("status_code") == "FINISHED":
                    break
                time.sleep(5)
            r = c.post(
                f"{GRAPH}/{self.ig_id}/media_publish",
                data={"creation_id": container, "access_token": self.page_token},
            )
            r.raise_for_status()
            return str(r.json()["id"])

    def upload(self, record: PostRecord, asset: Path) -> str:
        short = Path(record.asset.short_uri) if record.asset.short_uri else asset
        fb = self._facebook_reel(record, short)
        record.publish.platform_post_ids["facebook"] = fb
        record.publish.urls["facebook"] = f"https://www.facebook.com/reel/{fb}"
        base = os.environ.get("CAP_PUBLIC_BASE_URL", "").rstrip("/")
        if self.ig_id and base:
            try:
                ig = self._instagram_reel(record, f"{base}/assets/{record.post_id}/short.mp4")
                record.publish.platform_post_ids["instagram"] = ig
            except Exception as e:  # noqa: BLE001 - one platform failing does not undo the other
                record.publish.errors["instagram"] = f"{type(e).__name__}: {str(e)[:160]}"
        elif self.ig_id:
            record.publish.errors["instagram"] = (
                "Instagram needs the video at a public URL; not possible from the laptop"
            )
        return fb
