from .models import Reason


class ExecutionClosed(RuntimeError):
    """Raised when a terminal execution is used again."""


class LimitTrip(BaseException):
    """Internal control flow for expected limit violations."""

    def __init__(self, reason: Reason, detail: str):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail
