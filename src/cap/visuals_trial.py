"""`cap visuals trial`: one of your posts made again with real photographs, beside the
video it has now (Hemant, 25 Oct: the pictures are the problem; the voice, pace and
structure stay). Same script, same voice, same scene timing; only the pictures change.

Nothing in the app changes: the post, its card and its files stay as they are. The new
video, the side-by-side and the photo credits go to cap-data/visuals-trial/<post>/.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path

from cap.schema.post_record import PostRecord, PostStatus


def _pick(records: list[PostRecord], post_id: str | None) -> PostRecord | None:
    """The post asked for, else the newest published one, else the newest with a video."""
    if post_id:
        return next((r for r in records if r.post_id.startswith(post_id)), None)
    with_video = [r for r in records if r.asset.uri and Path(r.asset.uri).is_file()]
    published = [r for r in with_video if r.status == PostStatus.PUBLISHED]
    pool = published or with_video
    return max(pool, key=lambda r: r.history[-1] if r.history else "") if pool else None


def current_video(record: PostRecord) -> Path | None:
    """What the post has now: the final cut if it was approved, else the review cut."""
    if not record.asset.uri:
        return None
    review = Path(record.asset.uri)
    final = review.parent / "final" / review.name
    return final if final.is_file() else review if review.is_file() else None


def side_by_side(old: Path, new: Path, out: Path) -> Path:
    """Now on the left, new on the right, labelled; the new video's sound."""
    from PIL import Image, ImageDraw

    from cap.render.media import run_ffmpeg
    from cap.render.text import text_font

    strip = out.with_name("labels.png")
    im = Image.new("RGB", (1920, 90), (0x1B, 0x1A, 0x17))
    d = ImageDraw.Draw(im)
    f = text_font(44, bold=True)
    d.text((40, 18), "NOW", font=f, fill=(0xDD, 0xD9, 0xD0))
    d.text((1000, 18), "NEW: real photographs", font=f, fill=(0xFF, 0xFF, 0xFF))
    im.save(strip)
    graph = (
        "[0:v]scale=960:540,setsar=1[a];[1:v]scale=960:540,setsar=1[b];"
        "[a][b]hstack=inputs=2[pair];[2:v][pair]vstack=inputs=2:shortest=1[v]"
    )
    run_ffmpeg([
        "-i", str(old), "-i", str(new), "-loop", "1", "-i", str(strip),
        "-filter_complex", graph, "-map", "[v]", "-map", "1:a",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(out),
    ])  # fmt: skip
    strip.unlink(missing_ok=True)
    return out


def trial(post_id: str | None = None, out: Callable[[str], None] = print) -> int:
    from cap import localpg
    from cap.config import get_settings
    from cap.store.postgres import PostgresStore

    s = get_settings()
    data = Path(s.data_dir).resolve()
    with localpg.database_url(s.database_url, data, s.pg_bin, s.pg_port) as url:
        store = PostgresStore.open(url)
        try:
            record = _pick(store.all(), post_id)
        finally:
            store.close()
    if record is None:
        out("no post with a video to remake" + (f" (looked for {post_id})" if post_id else ""))
        return 1
    return remake(record, data / "visuals-trial", out)


def remake(record: PostRecord, root: Path, out: Callable[[str], None] = print) -> int:
    from cap.render import Stage, render
    from cap.visuals import Chooser

    old = current_video(record)
    stage: Stage = "final" if record.human_contribution.text.strip() else "review"
    out(f"remaking '{record.draft.title}' ({record.channel_id.value}) with real photographs")
    chooser = Chooser(
        record.brief.topic or record.draft.title, root / record.post_id / "pictures", out
    )
    out("voicing and drawing the scenes (a few minutes)...")
    result = render(record, root, stage=stage, pictures=chooser, with_short=False)
    folder = root / record.post_id
    credits = folder / "credits.txt"
    credits.write_text(
        "\n".join(f"{p.credit}\n  {p.page}" for p in chooser.pictures) + "\n", encoding="utf-8"
    )
    out(f"new video: {result.video}")
    out(f"photo credits: {credits} ({len(chooser.pictures)} photographs)")
    if old is None:
        out("this post has no earlier video to compare with")
        return 0
    compare = side_by_side(old, result.video, folder / "compare.mp4")
    out(f"side by side: {compare}")
    if sys.platform == "win32":
        os.startfile(compare)  # type: ignore[attr-defined]  # noqa: S606 - opens his own file
    return 0
