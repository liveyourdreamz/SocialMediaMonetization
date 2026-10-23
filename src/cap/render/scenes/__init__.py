"""Scenes: the picture of an episode, planned from the approved record and drawn frame
by frame.

Each spoken paragraph becomes one scene. The character stands on screen and speaks
(the lens or beacon follows the voice); the screen panel beside it shows what the
paragraph is about: the app's sync bar failing, a stopwatch running to the measured
number, the local-then-cloud flow, the seven-day strip, the verdict stamp, a figure
from a source. What is shown is chosen from the words of the script and the claims,
never invented beyond them, and every reconstructed app view is labelled as such.
Uploaded screenshots in the brief take the panel instead when present.

Layout, landscape: character left, screen panel right, the spoken sentence below.
Portrait (the Short): panel top, character bottom-left, sentence beside it.

The package: model (palette, Scene), plan, draw, panels_app, panels_tally, panels
(the dispatch), frame."""

from __future__ import annotations

from cap.render.scenes.draw import Layout, layout
from cap.render.scenes.frame import frame, loudness, thumbnail
from cap.render.scenes.model import FPS, STOP, Scene
from cap.render.scenes.plan import plan, tool_name, units

__all__ = [
    "FPS",
    "Layout",
    "STOP",
    "Scene",
    "frame",
    "layout",
    "loudness",
    "plan",
    "thumbnail",
    "tool_name",
    "units",
]
