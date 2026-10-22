"""One render of a post's package, review or final, with whichever renderer is on.

Why this exists: the pipeline renders before approval (the review render the card
plays) and the release job renders again after approval (the final render, with the
approver's opening line spoken). Both must make the same kind of files the same way
and choose the voice by the same rule, so the choice lives here once and the two
callers only say which stage they want.

The text renderer is the Foundation stand-in the tests use: a UTF-8 file of the
title and the narration, written as bytes (no newline translation on Windows).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from cap.config import get_settings
from cap.render import Stage, script_text
from cap.schema.post_record import PostRecord

Kind = Literal["text", "video"]
Role = Literal["long", "short", "thumbnail"]


@dataclass
class Rendered:
    kind: Kind
    files: dict[Role, Path] = field(default_factory=dict)
    sha256: str = ""  # of the long-form file
    duration_s: float = 0.0
    voice: str = ""


def render_stage(record: PostRecord, out_dir: Path, stage: Stage, kind: Kind) -> Rendered:
    """Render `stage` of the package under `out_dir`. Raises on failure (RenderError,
    the voice service, ffmpeg); the caller decides whether that parks or fails."""
    out_dir.mkdir(parents=True, exist_ok=True)
    if kind == "video":
        from cap.render import render, silent_voice, speak

        voice_fn = speak if get_settings().voice == "edge" else silent_voice
        r = render(record, out_dir, voice_fn=voice_fn, stage=stage)
        files: dict[Role, Path] = {"long": r.video, "thumbnail": r.thumbnail}
        if r.short:
            files["short"] = r.short
        return Rendered("video", files, r.sha256, r.duration_s, r.voice)
    suffix = "final.txt" if stage == "final" else "render.txt"
    path = out_dir / f"{record.post_id}.{suffix}"
    data = f"{record.draft.title}\n\n{script_text(record, stage=stage)}\n".encode()
    path.write_bytes(data)
    return Rendered("text", {"long": path}, hashlib.sha256(data).hexdigest())


def render_root(record: PostRecord) -> Path:
    """Where the review render was written, so the final render sits beside it:
    <root>/<post_id>.render.txt (text) or <root>/<post_id>/long.mp4 (video)."""
    if record.asset.uri:
        p = Path(record.asset.uri)
        return p.parent.parent if record.asset.kind == "video" else p.parent
    return Path(get_settings().data_dir) / "renders"
