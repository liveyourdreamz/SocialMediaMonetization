"""Reading stored post records across builds (review H8).

The record model forbids unknown fields, because the same strict base validates what
the models return. A file on disk is different: a build that dropped or renamed a
field leaves files that the new model rejects, and one such file used to stop the app
starting. `load_record` migrates a file to the current SCHEMA_VERSION one step at a
time, then validates it with every unknown field set aside (and reported), so only a
file that is really broken is refused.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from cap.schema.post_record import SCHEMA_VERSION, PostRecord

Migration = Callable[[dict[str, Any]], dict[str, Any]]


def _v1_to_v2(data: dict[str, Any]) -> dict[str, Any]:
    """v1: every file written before the record carried schema_version. Same layout."""
    return {**data, "schema_version": 2}


# MIGRATIONS[n] turns a version-n record into a version n+1 record.
MIGRATIONS: dict[int, Migration] = {1: _v1_to_v2}


@dataclass
class Loaded:
    record: PostRecord
    from_version: int
    dropped: list[str] = field(default_factory=list)  # unknown fields set aside, dotted

    @property
    def changed(self) -> bool:
        return bool(self.dropped) or self.from_version != SCHEMA_VERSION


def migrate(data: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Bring `data` up to SCHEMA_VERSION. Returns it and the version it started at.
    A record from a newer build is left as it is (its extra fields are dropped later)."""
    start = version = int(data.get("schema_version", 1))
    while version < SCHEMA_VERSION:
        data = MIGRATIONS[version](data)
        if int(data["schema_version"]) != version + 1:
            raise ValueError(f"migration from v{version} did not produce v{version + 1}")
        version += 1
    if version > SCHEMA_VERSION:  # written by a newer build; read as this build's layout
        data = {**data, "schema_version": SCHEMA_VERSION}
    return data, start


def _drop(data: Any, loc: tuple[int | str, ...]) -> bool:
    """Delete the value at `loc`; segments that are not keys (union tags) are skipped."""
    node = data
    for seg in loc[:-1]:
        if isinstance(node, dict) and seg in node:
            node = node[seg]
        elif isinstance(node, list) and isinstance(seg, int) and seg < len(node):
            node = node[seg]
    if isinstance(node, dict) and loc[-1] in node:
        del node[loc[-1]]
        return True
    return False


def load_record(text: str) -> Loaded:
    """Parse one stored record. Raises ValueError (JSON or validation) when it cannot."""
    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError("a post record must be a JSON object")
    data, start = migrate(raw)
    dropped: list[str] = []
    while True:
        try:
            return Loaded(PostRecord.model_validate(data), start, dropped)
        except ValidationError as e:
            extra = [err["loc"] for err in e.errors() if err["type"] == "extra_forbidden"]
            removed = [loc for loc in extra if _drop(data, loc)]
            if not removed:
                raise
            dropped += [".".join(str(s) for s in loc) for loc in removed]
