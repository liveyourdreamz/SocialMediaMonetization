"""YouTube adapter: the real upload, through Hemant's own Google OAuth client.

Set-up, once, on the laptop: `cap youtube auth` opens the browser, Hemant signs in to
the channel's Google account and grants youtube.upload; the refresh token is kept in
<data_dir>/youtube-token.json (git-ignored). Then CAP_PUBLISHER=youtube.

What is uploaded comes from the verified release manifest (cap.release), never from
the record: one adapter per cut ("long": the episode with the thumbnail; "short":
the Short, titled with #Shorts), each with its variant's title, description and
tags, and the manifest's flags for made-for-kids (D12), synthetic media (R-16) and
paid promotion. YouTube's limits are applied here, the same way every time: titles
cut at a word under 100 characters (room left for " #Shorts"), no < or > in titles or
descriptions, descriptions cut to 5000 UTF-8 bytes. Privacy defaults to unlisted so
the first uploads can be checked on the channel before anyone sees them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from cap.config import get_settings
from cap.publisher import Upload
from cap.release import Manifest, PlatformFlags, description_for

__all__ = ["YouTubeAdapter", "description_for", "youtube_description", "youtube_title"]

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


TITLE_MAX = 100
DESCRIPTION_MAX_BYTES = 5000
SHORTS_TAG = " #Shorts"


def _no_angles(text: str) -> str:
    return text.replace("<", "").replace(">", "")


def youtube_title(title: str, short: bool = False) -> str:
    """At most 100 characters with the #Shorts tag included, cut at a word, no < or >
    (YouTube refuses them)."""
    clean = " ".join(_no_angles(title).split())
    room = TITLE_MAX - (len(SHORTS_TAG) if short else 0)
    if len(clean) > room:
        head = clean[: room + 1]  # one more, so a word ending exactly at the limit stays
        clean = (head.rsplit(" ", 1)[0] if " " in head else clean[:room]).rstrip()
    return clean + (SHORTS_TAG if short else "")


def youtube_description(text: str) -> str:
    """No < or >, at most 5000 bytes of UTF-8 (an Arabic character is two), never a
    character cut in half."""
    data = _no_angles(text).encode("utf-8")[:DESCRIPTION_MAX_BYTES]
    return data.decode("utf-8", "ignore")


def video_body(
    title: str, description: str, tags: tuple[str, ...], flags: PlatformFlags, privacy: str
) -> dict[str, dict[str, object]]:
    """The videos.insert body: the texts and every declaration from the manifest."""
    return {
        "snippet": {
            "title": title,
            "description": youtube_description(description),
            "tags": list(tags[:20]),
            "categoryId": "27",  # Education
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": flags.made_for_kids,
            "containsSyntheticMedia": flags.synthetic_media,
        },
        "paidProductPlacementDetails": {"hasPaidProductPlacement": flags.paid_promotion},
    }


class YouTubeAdapter:
    """One cut per adapter, so each is recorded and retried on its own (review H5)."""

    def __init__(self, cut: Literal["long", "short"] = "long") -> None:
        self.cut = cut
        self.name = "youtube" if cut == "long" else "youtube_short"
        self.privacy = get_settings().youtube_privacy

    def _upload_one(self, yt, path: Path, body: dict[str, dict[str, object]]) -> str:  # type: ignore[no-untyped-def]
        from googleapiclient.http import MediaFileUpload

        media = MediaFileUpload(
            str(path), chunksize=8 * 1024 * 1024, resumable=True, mimetype="video/mp4"
        )
        req = yt.videos().insert(part=",".join(body), body=body, media_body=media)
        resp = None
        while resp is None:
            _, resp = req.next_chunk()
        return str(resp["id"])

    def upload(self, release: Manifest) -> Upload:
        if self.cut == "short" and release.file("short") is None:
            raise RuntimeError("this release has no Short")
        yt = _service()
        text = release.text_for("youtube_short" if self.cut == "short" else "youtube_long")
        body = video_body(
            youtube_title(text.title, short=self.cut == "short"),
            text.description,
            text.tags,
            release.flags,
            self.privacy,
        )
        vid = self._upload_one(yt, release.video(self.cut).path, body)
        if self.cut == "short":
            return Upload(vid, f"https://www.youtube.com/shorts/{vid}")
        errors: dict[str, str] = {}
        thumb = release.file("thumbnail")
        if thumb is not None:
            try:
                from googleapiclient.http import MediaFileUpload

                yt.thumbnails().set(
                    videoId=vid, media_body=MediaFileUpload(str(thumb.path), mimetype="image/png")
                ).execute()
            except Exception as e:  # noqa: BLE001 - a thumbnail is not worth failing the publish for
                errors["youtube_thumbnail"] = f"thumbnail not set: {type(e).__name__}"
        return Upload(vid, f"https://www.youtube.com/watch?v={vid}", errors=errors)


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

    from cap.render.media import ffmpeg_exe

    clip = Path(tempfile.mkdtemp(prefix="cap-yt-test-")) / "cap-connection-test.mp4"
    subprocess.run(
        [
            ffmpeg_exe(), "-y", "-loglevel", "error",
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
        body = video_body(
            "CAP connection test (private, safe to delete)",
            "A 5-second test upload from the Content Agent Portfolio app. Private. "
            "Delete it from YouTube Studio whenever you like.",
            ("test",),
            PlatformFlags(made_for_kids=False, synthetic_media=True, paid_promotion=False),
            "private",  # a test is never public, whatever the setting says
        )
        vid = adapter._upload_one(yt, clip, body)
    except Exception as e:  # noqa: BLE001 - one plain line, then a non-zero exit
        out(f"FAIL youtube upload: {explain(e)}")
        return 1
    out(f"ok   uploaded PRIVATE: https://www.youtube.com/watch?v={vid}")
    out("     it is visible only to you; YouTube may take a minute to finish processing")
    out("     delete it in YouTube Studio > Content when you have seen it")
    return 0
