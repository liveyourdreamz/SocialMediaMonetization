"""TikTok adapter: Content Posting API, direct post from a file upload (chunked).

Unaudited developer apps may post only to the connected account, as private; the
privacy level is SELF_ONLY until the app passes TikTok's audit. Not yet run against a
live account by us: the Connections screen says so.
"""

from __future__ import annotations

import time
from pathlib import Path

import httpx

from cap import connections
from cap.schema.post_record import PostRecord

API = "https://open.tiktokapis.com/v2"
CHUNK = 10 * 1024 * 1024


class TikTokAdapter:
    name = "tiktok"

    def __init__(self) -> None:
        data = connections.load("tiktok")
        tok = data.get("token") or {}
        self.token = tok.get("access_token", "")
        if not self.token:
            raise RuntimeError("TikTok is not connected; open Connections in the app")

    def upload(self, record: PostRecord, asset: Path) -> str:
        video = Path(record.asset.short_uri) if record.asset.short_uri else asset
        size = video.stat().st_size
        chunk = min(CHUNK, size)
        n_chunks = max(1, (size + chunk - 1) // chunk)
        headers = {"Authorization": f"Bearer {self.token}"}
        with httpx.Client(timeout=120) as c:
            r = c.post(
                f"{API}/post/publish/video/init/",
                headers={**headers, "Content-Type": "application/json; charset=UTF-8"},
                json={
                    "post_info": {
                        "title": record.draft.title[:150],
                        "privacy_level": "SELF_ONLY",
                        "disable_duet": False,
                        "disable_comment": False,
                        "disable_stitch": False,
                        "is_aigc": True,
                    },
                    "source_info": {
                        "source": "FILE_UPLOAD",
                        "video_size": size,
                        "chunk_size": chunk,
                        "total_chunk_count": n_chunks,
                    },
                },
            )
            r.raise_for_status()
            d = r.json().get("data") or {}
            publish_id, upload_url = d.get("publish_id"), d.get("upload_url")
            if not publish_id or not upload_url:
                raise RuntimeError(f"TikTok init: {r.json().get('error')}")
            with video.open("rb") as f:
                offset = 0
                while offset < size:
                    blob = f.read(chunk)
                    end = offset + len(blob) - 1
                    up = c.put(
                        upload_url,
                        headers={
                            "Content-Type": "video/mp4",
                            "Content-Length": str(len(blob)),
                            "Content-Range": f"bytes {offset}-{end}/{size}",
                        },
                        content=blob,
                    )
                    if up.status_code not in (200, 201, 206):
                        raise RuntimeError(f"TikTok upload chunk: HTTP {up.status_code}")
                    offset += len(blob)
            for _ in range(30):
                s = c.post(
                    f"{API}/post/publish/status/fetch/",
                    headers={**headers, "Content-Type": "application/json; charset=UTF-8"},
                    json={"publish_id": publish_id},
                )
                st = (s.json().get("data") or {}).get("status")
                if st in ("PUBLISH_COMPLETE", "FAILED"):
                    if st == "FAILED":
                        raise RuntimeError(f"TikTok: {s.json().get('data')}")
                    break
                time.sleep(3)
        record.publish.urls["tiktok"] = "https://www.tiktok.com/ (private until the app is audited)"
        return str(publish_id)
