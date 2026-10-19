"""The four parallel gates (PRD section 3). All four run before the reviewer sees a post;
their outcomes are hidden on the card until a verdict is recorded (D33)."""

from cap.gates.disclosure import check_disclosure
from cap.gates.ethics import check_ethics
from cap.gates.facts import Source, check_facts
from cap.gates.originality import InMemoryHistory, check_originality

__all__ = [
    "InMemoryHistory",
    "Source",
    "check_disclosure",
    "check_ethics",
    "check_facts",
    "check_originality",
]
