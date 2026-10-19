"""Rulebook loader. The YAML is version-locked; the judges receive the judge-rules
as text, the gates read the code-rules' parameters."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

JudgedBy = Literal["code", "judge", "ops"]


class Rule(BaseModel):
    id: str
    title: str
    judged_by: JudgedBy
    rule: str
    garp: list[str] = Field(default_factory=list)
    lever: Literal["red", "amber", "control"] = "red"
    calibrate: bool = False
    params: dict[str, float | int | str] = Field(default_factory=dict)


class Rulebook(BaseModel):
    version: str
    severity_order: list[str]
    rules: list[Rule]

    def by_id(self, rule_id: str) -> Rule:
        for r in self.rules:
            if r.id == rule_id:
                return r
        raise KeyError(rule_id)

    def judged_by(self, who: JudgedBy) -> list[Rule]:
        return [r for r in self.rules if r.judged_by == who]

    def as_prompt_text(self) -> str:
        """The text the ethics judges receive inside <rulebook>."""
        lines = [f"Rulebook version {self.version}."]
        for r in self.rules:
            lines.append(f"{r.id} ({r.title}; judged_by: {r.judged_by}): {r.rule}")
        return "\n".join(lines)


DEFAULT_PATH = Path(__file__).with_name("rulebook_v1.yaml")


@lru_cache
def load_rulebook(path: Path = DEFAULT_PATH) -> Rulebook:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Rulebook.model_validate(data)
