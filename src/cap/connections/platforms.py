"""The platforms a post can go to: what each needs from its developer console, its
OAuth endpoints and scopes, and the destinations the Outbox offers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlatformSpec:
    key: str
    label: str
    destinations: tuple[str, ...]  # the cuts it can carry
    fields: tuple[tuple[str, str], ...]  # (field, label) credentials to capture
    console: str  # where the credentials come from
    console_note: str
    auth_url: str
    token_url: str
    scopes: str
    verified: bool  # has the adapter been run against a live account by us


PLATFORMS: dict[str, PlatformSpec] = {
    "youtube": PlatformSpec(
        key="youtube",
        label="YouTube",
        destinations=("youtube_long", "youtube_short"),
        fields=(("client_id", "OAuth client id"), ("client_secret", "OAuth client secret")),
        console="https://console.cloud.google.com/apis/credentials",
        console_note=(
            "Google Cloud console > APIs & Services > Credentials > Create OAuth client "
            "(Web application). Enable the YouTube Data API v3 on the project. The same "
            "client also serves Google sign-in. Then Google Auth Platform > Audience: while "
            "the app's publishing status is Testing, add your own Google account under Test "
            "users or Google refuses the sign-in (403 access_denied); Testing also expires "
            "the token every 7 days, so once Connect works, press Publish app there. "
            "Unverified is fine for your own account: click through the warning once."
        ),
        auth_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        scopes="https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly",
        verified=False,
    ),
    "meta": PlatformSpec(
        key="meta",
        label="Facebook Page and Instagram",
        destinations=("facebook", "instagram"),
        fields=(("app_id", "Meta app id"), ("app_secret", "Meta app secret")),
        console="https://developers.facebook.com/apps/",
        console_note=(
            "Meta for Developers > your app (type Business) > Facebook Login for Business. "
            "The Instagram account must be a professional account linked to the Facebook "
            "Page. Instagram publishing needs the video at a public URL, so on the laptop "
            "only the Facebook Reel is sent."
        ),
        auth_url="https://www.facebook.com/v21.0/dialog/oauth",
        token_url="https://graph.facebook.com/v21.0/oauth/access_token",
        scopes="pages_show_list,pages_manage_posts,pages_read_engagement,instagram_basic,instagram_content_publish,business_management",
        verified=False,
    ),
    "tiktok": PlatformSpec(
        key="tiktok",
        label="TikTok",
        destinations=("tiktok",),
        fields=(("client_key", "Client key"), ("client_secret", "Client secret")),
        console="https://developers.tiktok.com/apps/",
        console_note=(
            "TikTok for Developers > Manage apps > add the Content Posting API product "
            "and request the video.publish scope; unaudited apps can post to the "
            "connected account only as private."
        ),
        auth_url="https://www.tiktok.com/v2/auth/authorize/",
        token_url="https://open.tiktokapis.com/v2/oauth/token/",
        scopes="user.info.basic,video.publish,video.upload",
        verified=False,
    ),
}


DESTINATION_LABELS = {
    "local": "Local folder (always; the record of what went out)",
    "youtube_long": "YouTube: the episode",
    "youtube_short": "YouTube: the Short",
    "facebook": "Facebook Page: Reel",
    "instagram": "Instagram: Reel",
    "tiktok": "TikTok",
}
