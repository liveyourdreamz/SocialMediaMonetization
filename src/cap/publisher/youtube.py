"""YouTube adapter: the real upload, through Hemant's own Google OAuth client.

Set-up, once, on the laptop: `cap youtube auth` opens the browser, Hemant signs in to
the channel's Google account and grants youtube.upload; the refresh token is kept in
<data_dir>/youtube-token.json (git-ignored). Then CAP_PUBLISHER=youtube.

What is uploaded: the long-form mp4 (the file the approval token is bound to), with
the title, the description (caption, sources, the AI-character disclosure), tags, the
thumbnail, `selfDeclaredMadeForKids=false` (D12) and `containsSyntheticMedia=true`
(disclosure, R-16); the Short as a second video when one exists. Privacy defaults to
unlisted so the first uploads can be checked on the channel before anyone sees them.
"""

from __future__ import annotations

import json
from pathlib import Path

from cap.config import get_settings
from cap.policy import AI_ROLE_DISCLOSURE
from cap.schema.post_record import PostRecord

YOUTUBE_AUTH_PORT = 8765  # register http://localhost:8765/ on the OAuth client
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def token_path() -> Path:
    return Path(get_settings().data_dir) / "youtube-token.json"


def authorise() -> Path:
    """Interactive, once: the browser flow against Hemant's OAuth client."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    s = get_settings()
    if not s.google_enabled:
        raise RuntimeError("CAP_GOOGLE_CLIENT_ID and CAP_GOOGLE_CLIENT_SECRET are needed first")
    assert s.google_client_secret is not None
    client_config = {
        "installed": {
            "client_id": s.google_client_id,
            "client_secret": s.google_client_secret.get_secret_value(),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [f"http://localhost:{YOUTUBE_AUTH_PORT}/"],
        }
    }
    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    # a fixed port: a Web OAuth client needs this exact redirect URI registered
    creds = flow.run_local_server(port=YOUTUBE_AUTH_PORT, prompt="consent")
    out = token_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(creds.to_json(), encoding="utf-8")
    return out


def _service():  # type: ignore[no-untyped-def]
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    path = token_path()
    if not path.exists():
        raise RuntimeError("no YouTube credentials; run `cap youtube auth` first")
    creds = Credentials.from_authorized_user_info(
        json.loads(path.read_text(encoding="utf-8")), SCOPES
    )
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        path.write_text(creds.to_json(), encoding="utf-8")
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def description_for(record: PostRecord) -> str:
    sources = sorted(
        {
            c.source_url
            for c in record.draft.claims
            if c.source_url and not c.source_url.startswith("testlog://")
        }
    )
    lines = [record.draft.caption.strip(), ""]
    if sources:
        lines += ["Sources:"] + sources + [""]
    if AI_ROLE_DISCLOSURE not in record.draft.caption:
        lines.append(AI_ROLE_DISCLOSURE)
    lines.append(
        "Voice and visuals are synthetic; the script is reviewed and approved by a person before publishing."
    )
    return "\n".join(lines).strip()


class YouTubeAdapter:
    name = "youtube"

    def __init__(self) -> None:
        self.privacy = get_settings().youtube_privacy

    def _upload_one(self, yt, path: Path, title: str, description: str, tags: list[str]) -> str:  # type: ignore[no-untyped-def]
        from googleapiclient.http import MediaFileUpload

        body = {
            "snippet": {
                "title": title[:100],
                "description": description[:5000],
                "tags": tags[:20],
                "categoryId": "27",
            },  # 27 = Education
            "status": {
                "privacyStatus": self.privacy,
                "selfDeclaredMadeForKids": False,
                "containsSyntheticMedia": True,
            },
        }
        media = MediaFileUpload(
            str(path), chunksize=8 * 1024 * 1024, resumable=True, mimetype="video/mp4"
        )
        req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
        resp = None
        while resp is None:
            _, resp = req.next_chunk()
        return str(resp["id"])

    def upload(self, record: PostRecord, asset: Path) -> str:
        yt = _service()
        description = description_for(record)
        tags = [t for t in record.brief.topic.split() if len(t) > 3][:10]
        vid = self._upload_one(yt, asset, record.draft.title, description, tags)
        thumb = Path(record.asset.thumbnail_uri) if record.asset.thumbnail_uri else None
        if thumb and thumb.exists():
            try:
                from googleapiclient.http import MediaFileUpload

                yt.thumbnails().set(
                    videoId=vid, media_body=MediaFileUpload(str(thumb), mimetype="image/png")
                ).execute()
            except Exception as e:  # a thumbnail is not worth failing the publish for
                record.log(f"thumbnail not set: {type(e).__name__}")
        short = Path(record.asset.short_uri) if record.asset.short_uri else None
        if short and short.exists():
            cut = next((v for v in record.variants if v.platform.value == "youtube_short"), None)
            title = (cut.title if cut and cut.title else record.draft.title) + " #Shorts"
            sid = self._upload_one(yt, short, title, description, tags)
            record.publish.platform_post_ids["youtube_short"] = sid
        return vid
