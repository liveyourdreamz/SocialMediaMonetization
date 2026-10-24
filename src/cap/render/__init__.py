"""The media renderer (agent 11, plain code), scrappy edition.

Voice: Microsoft neural voices through edge-tts (free, no key; English and Arabic).
    The PRD's ElevenLabs private voices (D21) replace this behind the same function.
Picture: scenes (cap.render.scenes). The character (Patch or Falak, drawn from the
    Anchor Registry, animated, speaking in time with the voice) stands beside a screen
    panel that shows what the paragraph is about: the app failing to sync, a stopwatch
    to the measured number, the local-then-cloud flow, the verdict stamp, a figure; or
    a screenshot uploaded with the brief. The spoken sentence runs underneath; the
    character tag and the "AI character" label are on every frame (R-16); a sources
    card ends it. No generated faces, no stock footage, nothing pretending to be a
    person; reconstructed app views are labelled as reconstructions.
Assembly: ffmpeg, the static binary that imageio-ffmpeg ships (cap.render.media).
Output: the long-form 1920x1080 mp4, a 1080x1920 Short when the post has a Short
    cut, a thumbnail png. Twice per post: the review render before approval (the
    opening line a visible placeholder) and the final render after it (the line
    spoken); the approval token binds the final files through the release manifest
    (cap.release). C2PA signing (D29) happens on the final release (cap.render.provenance).

Everything the picture shows comes from the approved record; the renderer never
invents words. Rendering is deterministic apart from the voice service.

Modules: base (stages, result, script text), voice, media (ffmpeg), video (the
renderer), text (fonts and Arabic shaping), scenes (the picture), package (a stage's
files on disk).
"""

from __future__ import annotations

from cap.render.base import (
    OPENING_PLACEHOLDER,
    OPENING_SLOT,
    PLACEHOLDER_UNIT,
    RenderError,
    RenderResult,
    Stage,
    paragraphs,
    script_text,
)
from cap.render.video import render
from cap.render.voice import VOICES, silent_voice, speak

__all__ = [
    "OPENING_PLACEHOLDER",
    "OPENING_SLOT",
    "PLACEHOLDER_UNIT",
    "VOICES",
    "RenderError",
    "RenderResult",
    "Stage",
    "paragraphs",
    "render",
    "script_text",
    "silent_voice",
    "speak",
]
