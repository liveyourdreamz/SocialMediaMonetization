"""Which channel a topic belongs to, when the brief says "Auto".

D42: both characters explain anything. Patch (A) takes things apart up close: objects,
technology, everyday how-it-works. Falak (B) sees things from orbit: earth, sky,
weather, oceans, cities, space, large-scale engineering. Channel C (Falak in Arabic)
is chosen only explicitly. With the API key a fast model decides in one word;
without one, keywords decide.
"""

from __future__ import annotations

import re

from cap.config import get_settings

_ORBIT_WORDS = re.compile(
    r"\b(earth|planet|space|orbit|satellite|moon|sun|star|stars|sky|weather|climate|"
    r"rain|rainbow|rainbows|cloud|clouds|storm|hurricane|monsoon|ocean|oceans|sea|tide|"
    r"tides|river|desert|glacier|volcano|earthquake|forest|city|cities|desalination|"
    r"dam|bridge|canal|port|aurora|eclipse|season|seasons|wind|el nino)\b",
    re.I,
)


def _heuristic(topic: str) -> str:
    return "B" if _ORBIT_WORDS.search(topic) else "A"


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
                    "Two explainer channels. A (Patch, a repair robot) takes things apart "
                    "up close: objects, devices, technology, the body's machinery, everyday "
                    "how-it-works. B (Falak, a satellite) explains what is best seen from "
                    "above: earth, weather, oceans, space, cities, large engineering. Pick "
                    "the better fit for the topic. Answer with the single letter A or B."
                ),
                messages=[{"role": "user", "content": topic[:300]}],
            )
            text = "".join(getattr(b, "text", "") for b in r.content).strip().upper()
            if text[:1] in ("A", "B"):
                return text[:1], "chosen by the model"
        except Exception:  # noqa: BLE001 - keywords decide instead
            pass
    return _heuristic(topic), "chosen by keywords"
