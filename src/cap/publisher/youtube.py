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
    try:
        import os
        import stat

        os.chmod(out, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return out


def _service():  # type: ignore[no-untyped-def]
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    from cap import connections

    info = connections.youtube_credentials_json()
    path = token_path()
    if info is None and path.exists():  # the older `cap youtube auth` file
        info = json.loads(path.read_text(encoding="utf-8"))
    if info is None:
        raise RuntimeError("YouTube is not connected; open Connections in the app")
    creds = Credentials.from_authorized_user_info(info, info.get("scopes") or SCOPES)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        connections.save_youtube_token(json.loads(creds.to_json()))
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def description_for(record: PostRecord) -> str:
    sources = sorted(
        {
            c.source_url
            for c in record.draft.claims
            if c.source_url
            and not c.source_url.startswith("testlog://")
            and c.status == "supported"  # never publish a link that confirmed nothing
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
        record.publish.urls["youtube"] = f"https://www.youtube.com/watch?v={vid}"
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
            record.publish.urls["youtube_short"] = f"https://www.youtube.com/shorts/{sid}"
        return vid


# What YouTube's error reasons mean for Hemant, and what to do about each.
_ADVICE = {
    "quotaExceeded": "the project's daily YouTube quota is used up; try again tomorrow",
    "uploadLimitExceeded": "this channel hit YouTube's upload limit; verify the channel "
    "by phone at youtube.com/verify, or wait a day",
    "youtubeSignupRequired": "this Google account has no YouTube channel yet; create one "
    "at youtube.com/create_channel, then reconnect",
    "forbidden": "the sign-in lacks upload permission; reconnect YouTube on Connections",
    "invalid_grant": "the saved sign-in expired or was revoked; reconnect YouTube on Connections",
    "accessNotConfigured": "the YouTube Data API v3 is not enabled for your Google Cloud "
    "project; enable it in the console, then retry",
}


def explain(e: Exception) -> str:
    """One line: what failed and what to do, never a secret."""
    text = str(e)
    reason = ""
    content = getattr(e, "content", b"") or b""
    try:
        err = json.loads(content.decode("utf-8", "replace")).get("error", {})
        reason = (err.get("errors") or [{}])[0].get("reason", "") or err.get("status", "")
        text = err.get("message", text)
    except (ValueError, AttributeError):
        pass
    for key, advice in _ADVICE.items():
        if key in (reason or "") or key in text:
            return f"{advice} (YouTube said: {text[:160]})"
    status = getattr(getattr(e, "resp", None), "status", "")
    return f"{type(e).__name__}{' ' + str(status) if status else ''}: {text[:300]}"


def test_upload(out=print) -> int:  # type: ignore[no-untyped-def]
    """A 5-second clip uploaded PRIVATE to the connected channel: proves the sign-in,
    the permission and the upload path with nothing public. Prints the link."""
    import subprocess
    import tempfile

    from cap.render import _ffmpeg_exe

    clip = Path(tempfile.mkdtemp(prefix="cap-yt-test-")) / "cap-connection-test.mp4"
    subprocess.run(
        [
            _ffmpeg_exe(), "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=15:duration=5",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(clip),
        ],
        check=True,
        capture_output=True,
    )  # fmt: skip
    out(f"made a 5-second test clip ({clip.stat().st_size // 1024} KB)")
    try:
        yt = _service()
        try:
            me = yt.channels().list(part="snippet", mine=True).execute()
            name = (me.get("items") or [{}])[0].get("snippet", {}).get("title", "?")
            out(f"signed in to the YouTube channel: {name}")
        except Exception:  # noqa: BLE001 - older sign-ins have upload permission only
            out("signed in (channel name not readable with this sign-in; fine for uploads)")
        adapter = YouTubeAdapter()
        adapter.privacy = "private"  # a test is never public, whatever the setting says
        vid = adapter._upload_one(
            yt,
            clip,
            "CAP connection test (private, safe to delete)",
            "A 5-second test upload from the Content Agent Portfolio app. Private. "
            "Delete it from YouTube Studio whenever you like.",
            ["test"],
        )
    except Exception as e:  # noqa: BLE001 - one plain line, then a non-zero exit
        out(f"FAIL youtube upload: {explain(e)}")
        return 1
    out(f"ok   uploaded PRIVATE: https://www.youtube.com/watch?v={vid}")
    out("     it is visible only to you; YouTube may take a minute to finish processing")
    out("     delete it in YouTube Studio > Content when you have seen it")
    return 0
