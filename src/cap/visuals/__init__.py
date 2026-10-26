"""Pictures for the video (Hemant, 25 Oct: "the visuals are appalling"; the sound, pace
and structure stay). Each spoken scene gets a real photograph of its subject, with a
slow pan and zoom, instead of a stock panel chosen by keywords.

director  what each scene should show, as search phrases (the model, our script only)
commons   Wikimedia Commons: search, the licence rule, a safe download
Chooser   the two together for one post: one picture per scene, none used twice,
          downloads kept so the long cut and the Short share them
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

import httpx

from cap.visuals import commons, director
from cap.visuals.commons import Picture

__all__ = ["Chooser", "Picture"]


class Chooser:
    """Callable the renderer takes: the scenes' spoken texts -> a Picture or None each.
    Remembers its answers, so the second cut asks no one."""

    def __init__(self, topic: str, folder: Path, say: Callable[[str], None] = print) -> None:
        self.topic, self.folder, self.say = topic, folder, say
        self.known: dict[str, Picture | None] = {}
        self.used: set[int] = set()

    @property
    def pictures(self) -> list[Picture]:
        return [p for p in self.known.values() if p is not None]

    def __call__(self, texts: list[str]) -> list[Picture | None]:
        todo = [t for t in dict.fromkeys(texts) if t not in self.known]
        if todo:
            self.say(f"choosing pictures for {len(todo)} scene(s)")
            plans = director.queries(self.topic, todo)
            with commons.client() as c:
                for text, phrases in zip(todo, plans, strict=True):
                    self.known[text] = self._one(c, text, phrases)
        return [self.known[t] for t in texts]

    def _one(self, c: httpx.Client, text: str, phrases: list[str]) -> Picture | None:
        for phrase in phrases:
            try:
                found = commons.search(c, phrase)
            except Exception as e:  # noqa: BLE001 - one failed search tries the next phrase
                self.say(f"  search '{phrase}' failed: {type(e).__name__}")
                continue
            for cand in found:
                if cand.page_id in self.used:
                    continue
                name = hashlib.sha256(cand.url.encode()).hexdigest()[:16] + ".jpg"
                try:
                    pic = commons.download(c, cand, self.folder / name)
                except Exception as e:  # noqa: BLE001 - try the next result
                    self.say(f"  skipped {cand.title[:60]}: {type(e).__name__}: {e}"[:160])
                    continue
                self.used.add(cand.page_id)
                self.say(f"  '{phrase}' -> {cand.title[5:65]} ({cand.licence})")
                return pic
        self.say(f"  no usable photo for: {text[:60]}…; this scene keeps its panel")
        return None
