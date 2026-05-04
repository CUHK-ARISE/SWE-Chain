from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict


class TaskType(StrEnum):
    FEATURE = "FEATURE"
    FIX = "FIX"
    DEPENDENCY = "DEPENDENCY"
    BREAKING = "BREAKING"
    TYPING = "TYPING"
    DOCUMENTATION = "DOCUMENTATION"
    PERFORMANCE = "PERFORMANCE"
    REFACTOR = "REFACTOR"
    OTHER = "OTHER"


class Significance(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Difficulty(StrEnum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class Expectation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    grounded: str
    conceptual: str


class Constraints(BaseModel):
    model_config = ConfigDict(extra="forbid")
    grounded: str
    conceptual: str


class SynthesizedRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    problem_statement: str
    expectation: Expectation
    constraints: Constraints
    behavior: list[str]
    acceptance_criteria: list[str]


class TaskSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str
    title: str
    type: TaskType
    runtime_impact: bool
    description: str
    synthesized_requirement: SynthesizedRequirement
    significance: Significance
    confidence: Confidence
    difficulty: Difficulty
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskSpec":
        normalized = dict(data)
        if normalized.get("type") not in TaskType._value2member_map_:
            normalized["type"] = TaskType.OTHER.value
        return cls.model_validate(normalized)
    
    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="python")
