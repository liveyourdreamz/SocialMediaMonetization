"""The Operator lists and the Outbox: the package, the final release and what each platform did."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cap.domain.verdict import confirmed_sources
from cap.schema.post_record import PostRecord, PostStatus
from cap.store import PostStore
from cap.ui.views.text import REASON_TEXT


def parked(store: PostStore) -> list[tuple[PostRecord, str]]:
    out = []
    for p in store.all():
        if p.status == PostStatus.PARKED:
            reason = next((h for h in reversed(p.history) if "parked: " in h), "")
            text = reason.split("parked: ", 1)[-1] if reason else "escalated by the reviewer"
            if p.approval.reason_code:
                text = f"{REASON_TEXT[p.approval.reason_code]} · {text}"
            out.append((p, text))
    return out


def published(store: PostStore) -> list[PostRecord]:
    out = [p for p in store.all() if p.status == PostStatus.PUBLISHED]
    return sorted(out, key=lambda p: p.publish.published_at or p.created_at, reverse=True)


def recent_briefs(store: PostStore) -> list[PostRecord]:
    """The last few posts in any state, newest first: what a brief turned into."""
    return sorted(store.all(), key=lambda p: p.created_at, reverse=True)[:8]


def outbox(store: PostStore) -> list[PostRecord]:
    """Approved, token issued, not yet published: what the publisher will send."""
    out = [p for p in store.all() if p.status == PostStatus.APPROVED and not p.flags.canary]
    return sorted(out, key=lambda p: p.approval.decided_at or p.created_at)


def _narration(record: PostRecord, cut: str | None = None) -> str:
    """The script as the final render voices it (the one rule: cap.render.script_text);
    before an opening line exists, as the review render shows it."""
    from cap.render import script_text

    if record.human_contribution.text.strip():
        return script_text(record, cut, "final")
    return script_text(record, cut, "review")


def package(record: PostRecord) -> dict[str, Any]:
    """Exactly what goes out, per platform, as the adapters will receive it."""
    script = _narration(record)
    base = {
        "title": record.draft.title,
        "caption": record.draft.caption,
        "thumbnail_text": record.draft.thumbnail_text,
        "on_screen_text": list(record.draft.on_screen_text),
        "script": script,
        "shot_list": list(record.draft.shot_list),
        "sources": confirmed_sources(record),
    }
    cuts = []
    for v in record.variants:
        cuts.append(
            {
                "platform": v.platform.value.replace("_", " "),
                "title": v.title or base["title"],
                "caption": v.caption or base["caption"],
                "duration_s": v.duration_s,
                "script": (_narration(record, v.script_cut) if v.script_cut else "") or script,
                "on_screen_text": list(v.on_screen_text) or base["on_screen_text"],
                "thumbnail_text": v.thumbnail_text or base["thumbnail_text"],
            }
        )
    if not cuts:  # no platform cuts recorded: the base package is the one cut
        cuts.append({"platform": "youtube long", "duration_s": 0, **base})
    final = next((Path(f.uri) for f in record.release.files if f.role == "long"), None)
    return {
        "base": base,
        "cuts": cuts,
        "asset_name": final.name if final else "",
        "asset_exists": bool(final and final.exists()),
        "sha256": record.release.manifest_sha256,
        "token_present": bool(record.approval.token),
    }


RELEASE_TEXT = {
    "none": "The final video has not been made yet.",
    "preparing": "Preparing the final video with your opening line.",
    "ready": "The final video is ready.",
    "failed": "The final video could not be made.",
}


def release_view(record: PostRecord) -> dict[str, Any]:
    """The Outbox's view of the final release: its state in words, the files to play,
    the token's state and which action is offered (make it, retry, re-issue)."""
    from cap.approval import release_job
    from cap.release import token_state

    rel = record.release
    status, error = rel.status.value, rel.error  # "preparing": a job is queued or running
    names = {f.role: Path(f.uri).name for f in rel.files}
    tstate = token_state(record) if status == "ready" else "none"
    return {
        "status": status,
        "text": RELEASE_TEXT[status],
        "error": error,
        "long": names.get("long", ""),
        "short": names.get("short", ""),
        "thumbnail": names.get("thumbnail", ""),
        "video": record.asset.kind == "video" and "long" in names,
        "token": tstate,
        "can_make": status in ("none", "failed") and release_job.can_start(record),
        "can_reissue": status == "ready" and tstate != "valid",
        "publishable": status == "ready" and tstate == "valid",
    }


# destination key -> the adapter that carries it (its result and error keys)
_ADAPTER_FOR = {
    "local": "local",
    "youtube_long": "youtube",
    "youtube_short": "youtube_short",
    "facebook": "facebook",
    "instagram": "instagram",
    "tiktok": "tiktok",
}


_ADAPTER_GROUP: dict[str, str] = {}  # one adapter per destination (22 Oct)


def publish_results(record: PostRecord) -> list[dict[str, Any]]:
    """Per chosen destination: sent / written / not sent, with the link or the reason."""
    from cap.connections import DESTINATION_LABELS

    out: list[dict[str, Any]] = []
    pub = record.publish
    for d in pub.destinations or ["local"]:
        key = _ADAPTER_FOR.get(d, d)
        group = _ADAPTER_GROUP.get(d, "")
        pid = pub.platform_post_ids.get(key) or (pub.platform_post_ids.get(group) if group else "")
        url = pub.urls.get(key) or pub.urls.get(d, "")
        err = pub.errors.get(key) or pub.errors.get(d) or (pub.errors.get(group) if group else "")
        if d == "local":
            state, text = ("written", pid) if pid else ("not written", err or "")
        elif pid and not err:
            state, text = "sent", url or pid
        elif err:
            state, text = "not sent", err
        else:
            state, text = "not sent", "no result recorded"
        out.append(
            {
                "key": d,
                "label": DESTINATION_LABELS.get(d, d),
                "state": state,
                "text": text,
                "url": url if state == "sent" else "",
            }
        )
    return out
