"""Rulebook loader. The YAML is version-locked; the judges receive the judge-rules
as text, the gates read the code-rules' parameters."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

JudgedBy = Literal["code", "judge", "both", "ops"]


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    judged_by: JudgedBy
    rule: str
    garp: list[str] = Field(default_factory=list)
    lever: Literal["red", "amber", "control"] = "red"
    calibrate: bool = False
    params: dict[str, float | int | str] = Field(default_factory=dict)


class Rulebook(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    severity_order: list[str]
    rules: list[Rule]

    def by_id(self, rule_id: str) -> Rule:
        for r in self.rules:
            if r.id == rule_id:
                return r
        raise KeyError(rule_id)

    def judged_by(self, who: JudgedBy) -> list[Rule]:
        """Rules for a given judge. `both` rules are returned for code and for judge."""
        return [r for r in self.rules if r.judged_by == who or r.judged_by == "both"]

    def params(self, rule_id: str) -> dict[str, float | int | str]:
        return self.by_id(rule_id).params

    def as_prompt_text(self) -> str:
        """The block the ethics judges receive, wrapped as the P1 prompt names it. The
        judge sees only `code` or `judge`: ops rules are shown as code (not theirs),
        both rules as judge (second net)."""
        shown = {"code": "code", "ops": "code", "judge": "judge", "both": "judge"}
        lines = [f'<rulebook version="{self.version}">']
        for r in self.rules:
            lines.append(f"{r.id} ({r.title}; judged_by: {shown[r.judged_by]}): {r.rule}")
        lines.append("</rulebook>")
        return "\n".join(lines)


DEFAULT_PATH = Path(__file__).with_name("rulebook_v1.yaml")


@lru_cache
def load_rulebook(path: Path = DEFAULT_PATH) -> Rulebook:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Rulebook.model_validate(data)
