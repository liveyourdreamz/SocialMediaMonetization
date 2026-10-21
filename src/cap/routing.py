"""Which channel a topic belongs to, when the brief says "Auto".

Patch (A) reviews AI tools and software by testing them; Falak (B) explains science
and engineering in English. Channel C (Falak in Arabic) is chosen only explicitly.
With the API key a fast model decides in one word; without one, keywords decide.
"""

from __future__ import annotations

import re

from cap.config import get_settings

_TOOL_WORDS = re.compile(
    r"\b(app|apps|tool|tools|software|saas|plugin|extension|chatgpt|claude|gemini|copilot|"
    r"notion|slack|zoom|excel|figma|canva|api|sdk|ide|editor|browser|chrome|ai\s+\w+|"
    r"version|update|release|subscription|pricing|tested|review|vs\.?)\b",
    re.I,
)


def _heuristic(topic: str) -> str:
    return "A" if _TOOL_WORDS.search(topic) else "B"


def route(topic: str) -> tuple[str, str]:
    """(channel, how it was decided)."""
    s = get_settings()
    if s.model_backend.value == "anthropic" and s.anthropic_api_key is not None:
        try:
            import anthropic

            from cap.prompts import HAIKU

            client = anthropic.Anthropic(
                api_key=s.anthropic_api_key.get_secret_value(),
                timeout=anthropic.Timeout(30.0, connect=10.0),
            )
            r = client.messages.create(
                model=HAIKU,
                max_tokens=5,
                system=(
                    "Classify a video topic for two channels. A: reviews of a specific AI "
                    "tool, app or piece of software, tested by the presenter. B: science, "
                    "nature, engineering or how-things-work explainers. Answer with the "
                    "single letter A or B."
                ),
                messages=[{"role": "user", "content": topic[:300]}],
            )
            text = "".join(getattr(b, "text", "") for b in r.content).strip().upper()
            if text[:1] in ("A", "B"):
                return text[:1], "chosen by the model"
        except Exception:  # noqa: BLE001 - keywords decide instead
            pass
    return _heuristic(topic), "chosen by keywords"
