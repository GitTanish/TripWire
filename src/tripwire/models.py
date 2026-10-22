from dataclasses import dataclass
from enum import Enum


class Status(str, Enum):
    COMPLETED = "completed"
    TRIPPED = "tripped"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Reason(str, Enum):
    COMPLETED = "completed"
    ITERATION_LIMIT = "iteration_limit"
    TOOL_BUDGET = "tool_budget"
    LOOP_DETECTED = "loop_detected"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass(frozen=True)
class Limits:
    max_iterations: int = 100
    max_tool_calls: int = 50
    timeout_seconds: float = 60.0
    repeat_threshold: int = 3


@dataclass(frozen=True)
class ExecutionResult:
    status: Status
    reason: Reason
    iterations: int
    tool_calls: int
    elapsed_seconds: float
    error: str | None = None
    detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is Status.COMPLETED
