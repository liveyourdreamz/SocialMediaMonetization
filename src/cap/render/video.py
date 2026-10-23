"""The video renderer: one stage of a post's package (the review cut before approval,
the final cut after it) as the long-form mp4, the Short when the post has a Short cut,
and the thumbnail.

Each spoken unit becomes a scene (cap.render.scenes); each scene is voiced, its frames
drawn and piped into ffmpeg as one segment; the segments are joined. The review cut
shows a silent "your opening line goes here" card where the approver's line will go;
the final cut speaks it.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from cap.config import get_settings
from cap.domain.verdict import confirmed_sources
from cap.render import scenes as sc
from cap.render.base import (
    PLACEHOLDER_UNIT,
    RenderError,
    RenderResult,
    Stage,
    paragraphs,
    script_text,
)
from cap.render.media import concat, duration, encode_scene
from cap.render.voice import VOICES, silence, speak
from cap.schema.post_record import Platform, PostRecord

Voice = Callable[[str, str, Path], None]
LONG, SHORT, THUMB = (1920, 1080), (1080, 1920), (1280, 720)
_SILENT = ("sources", "opening_slot")


@dataclass(frozen=True)
class _Episode:
    """What every scene of one render shares."""

    record: PostRecord
    work: Path
    character: str
    channel: str
    arabic: bool
    voice: str
    voice_fn: Voice
    sources: list[str]
    tool: str  # the app under test, named on Patch's thumbnail


def _episode(record: PostRecord, out_dir: Path, voice_fn: Voice, stage: Stage) -> _Episode:
    s = get_settings()
    channel = record.channel_id.value
    cast = s.channel_characters.get(channel, "")
    lang = s.channel_languages.get(channel, "en")
    work = out_dir / record.post_id / ("final" if stage == "final" else "")
    work.mkdir(parents=True, exist_ok=True)
    return _Episode(
        record=record,
        work=work,
        character=cast or "Patch",
        channel=channel,
        arabic=lang == "ar",
        voice=VOICES.get((cast, lang), "en-GB-RyanNeural"),
        voice_fn=voice_fn,
        sources=confirmed_sources(record),  # the one filter: supported claims, no test log
        tool=sc.tool_name(record.draft.title, record.brief.topic) if cast == "Patch" else "",
    )


def media_files(record: PostRecord) -> list[Path]:
    """Screenshots uploaded with the brief, in order, if any (cap-data/media/<post>/)."""
    d = Path(get_settings().data_dir) / "media" / record.post_id
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"))


def _plan_cut(ep: _Episode, text: str, title: str) -> list[sc.Scene]:
    """The scenes of one cut: the title spoken over the character, the story, the
    review placeholder as its own silent card, and the sources card at the end."""
    paras = paragraphs(text)
    if not paras:
        raise RenderError("nothing to say: the script is empty")
    said = sc.units(paras)
    # the review placeholder is planned around (not through) the scene rules, so the
    # review and final cuts share their pictures
    slot_at = said.index(PLACEHOLDER_UNIT) if PLACEHOLDER_UNIT in said else None
    if slot_at is not None:
        said.pop(slot_at)
    plan = sc.plan(
        [title, *said],
        character=ep.character,
        title=title,
        topic=ep.record.brief.topic,
        on_screen=list(ep.record.draft.on_screen_text),
        media=media_files(ep.record),
        arabic=ep.arabic,
    )
    plan[0].kind = "talk"
    if slot_at is not None:
        slot = sc.Scene(kind="opening_slot", text=PLACEHOLDER_UNIT, sentences=[PLACEHOLDER_UNIT])
        plan.insert(slot_at + 1, slot)
    plan.append(sc.Scene(kind="sources", text="", sentences=[]))
    return plan


def _frames(
    ep: _Episode, scene: sc.Scene, lay: sc.Layout, levels: list[float], t0: float
) -> Iterator[Image.Image]:
    n = len(levels)
    for k in range(n):
        yield sc.frame(
            scene, k, n, t0 + k / sc.FPS, lay,
            character=ep.character, channel=ep.channel,
            level=levels[k], arabic=ep.arabic, sources=ep.sources,
        )  # fmt: skip


def _scene_segment(
    ep: _Episode, scene: sc.Scene, lay: sc.Layout, mp3: Path, seg: Path, t0: float
) -> tuple[int, float]:
    """Voice one scene, draw its frames, encode them with the voice: (frames, seconds)."""
    silent = scene.kind in _SILENT
    if silent:
        silence(3.0, mp3)
    else:
        ep.voice_fn(scene.text, ep.voice, mp3)
    seconds = duration(mp3)
    n = max(2, int(round(seconds * sc.FPS)))
    levels = [0.0] * n if silent else sc.loudness(mp3, n)
    encode_scene(_frames(ep, scene, lay, levels, t0), sc.FPS, lay.size, mp3, seg)
    mp3.unlink(missing_ok=True)
    return n, seconds


def _cut(
    ep: _Episode, text: str, title: str, size: tuple[int, int], name: str
) -> tuple[Path, float]:
    """One cut, scene by scene, joined into <name>.mp4: (file, seconds)."""
    lay = sc.layout(size)
    segs: list[Path] = []
    t_run = total = 0.0
    for i, scene in enumerate(_plan_cut(ep, text, title)):
        seg = ep.work / f"{name}-{i:03d}.mp4"
        n, seconds = _scene_segment(ep, scene, lay, ep.work / f"{name}-{i:03d}.mp3", seg, t_run)
        segs.append(seg)
        t_run += n / sc.FPS
        total += seconds
    video = ep.work / f"{name}.mp4"
    concat(segs, video)
    for seg in segs:
        seg.unlink(missing_ok=True)
    return video, total


def render(
    record: PostRecord, out_dir: Path, *, voice_fn: Voice = speak, stage: Stage = "review"
) -> RenderResult:
    """Render one stage of the package into <out_dir>/<post_id>/ (review) or
    <out_dir>/<post_id>/final/ (final, after approval). `voice_fn` is injectable so
    tests run offline."""
    ep = _episode(record, out_dir, voice_fn, stage)
    video, seconds = _cut(ep, script_text(record, stage=stage), record.draft.title, LONG, "long")
    cut = next(
        (v for v in record.variants if v.platform == Platform.YOUTUBE_SHORT and v.script_cut), None
    )
    short = None
    if cut:
        text = script_text(record, cut.script_cut, stage)
        short, _ = _cut(ep, text, cut.title or record.draft.title, SHORT, "short")
    thumb = ep.work / "thumbnail.png"
    sc.thumbnail(
        THUMB,
        record.draft.thumbnail_text or record.draft.title,
        ep.character,
        ep.channel,
        ep.tool,
        ep.arabic,
    ).save(thumb)
    digest = hashlib.sha256(video.read_bytes()).hexdigest()
    return RenderResult(
        video=video, short=short, thumbnail=thumb, sha256=digest, duration_s=seconds, voice=ep.voice
    )
