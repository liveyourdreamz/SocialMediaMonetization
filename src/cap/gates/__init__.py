"""The four parallel gates (PRD section 3). All four run before the reviewer sees a post;
their outcomes are hidden on the card until a verdict is recorded (D33). What the four
results mean together (pass, questions for the approver, reject) is decided in one
place, cap.domain.verdict."""

from cap.domain.verdict import GateVerdict, Outcome, confirmed_sources, decide, gates_cleared
from cap.gates.disclosure import check_disclosure
from cap.gates.ethics import check_ethics
from cap.gates.facts import Source, check_facts
from cap.gates.history import (
    FileHistory,
    HistoryIndex,
    HistoryStore,
    InMemoryHistory,
    record_approved,
)
from cap.gates.originality import check_originality

__all__ = [
    "FileHistory",
    "GateVerdict",
    "HistoryIndex",
    "HistoryStore",
    "InMemoryHistory",
    "Outcome",
    "Source",
    "check_disclosure",
    "check_ethics",
    "check_facts",
    "check_originality",
    "confirmed_sources",
    "decide",
    "gates_cleared",
    "record_approved",
]
