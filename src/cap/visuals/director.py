"""The visual director: for each scene of the approved script, what to photograph.

It reads only our own script (no fetched text), and answers with search phrases, never
with pictures: the Commons search and the licence rule choose the picture
(cap.visuals.commons). A trial-stage prompt, kept here until the pictures are part of
the pipeline; then it moves into the Prompt Pack (P15) with a Pack version change.
"""

from __future__ import annotations

import json
from typing import Any

from cap.config import get_settings
from cap.domain.cost import note, usage_of
from cap.prompts import HAIKU

SYSTEM = (
    "You choose real photographs for a short narrated explainer video. For each "
    "numbered scene of the script, give three search phrases for Wikimedia Commons, "
    "best first. Each phrase is two to four English words naming something a camera "
    "can photograph: the thing itself, a place, an object, a natural process seen "
    "outdoors, or a spacecraft image (Commons holds NASA and ESA photographs). No "
    "abstract words (benefit, idea, reason), no diagrams, no charts, no logos, no "
    "people's faces unless the scene names a public figure. Follow the scene's own "
    "subject, so consecutive scenes show different things. The script may be in "
    "Arabic; the phrases are always English."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "scenes": {
            "type": "array",
            "items": {"type": "array", "items": {"type": "string"}},
        }
    },
    "required": ["scenes"],
    "additionalProperties": False,
}


def _ask(topic: str, scenes: list[str]) -> tuple[str, dict[str, Any]]:
    import anthropic

    s = get_settings()
    assert s.anthropic_api_key is not None
    c = anthropic.Anthropic(
        api_key=s.anthropic_api_key.get_secret_value(),
        timeout=anthropic.Timeout(120.0, connect=10.0),
        max_retries=2,
    )
    numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(scenes))
    resp = c.messages.create(
        model=HAIKU,
        max_tokens=2000,
        system=SYSTEM,
        messages=[{"role": "user", "content": f"Topic: {topic}\n\nScenes:\n{numbered}"}],
        output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},  # type: ignore[call-arg]
    )
    text = "".join(getattr(b, "text", "") for b in resp.content)
    return text, usage_of(resp)


def queries(topic: str, scenes: list[str]) -> list[list[str]]:
    """Search phrases per scene, best first; the topic itself is always the last resort.
    Without an API key (the mock backend) every scene searches the topic."""
    fallback = [topic]
    if get_settings().model_backend.value == "mock" or not scenes:
        return [fallback for _ in scenes]
    text, usage = _ask(topic, scenes)
    note("visual director", HAIKU, usage)
    try:
        got = json.loads(text).get("scenes", [])
    except (ValueError, AttributeError):
        got = []
    out = []
    for i in range(len(scenes)):
        phrases = got[i] if i < len(got) and isinstance(got[i], list) else []
        clean = [str(p).strip()[:60] for p in phrases if str(p).strip()][:3]
        out.append(clean + fallback)
    return out
