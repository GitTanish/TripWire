import asyncio
from collections import deque
from time import monotonic
from typing import Any, Awaitable, Callable, TypeVar

from .exceptions import ExecutionClosed, LimitTrip
from .models import ExecutionResult, Limits, Reason, Status

T = TypeVar("T")
AsyncTool = Callable[..., Awaitable[T]]
Workflow = Callable[["Execution"], Awaitable[Any]]


def _normalize(value: Any) -> Any:
    if isinstance(value, dict):
        items = ((_normalize(key), _normalize(item)) for key, item in value.items())
        return ("mapping", tuple(sorted(items, key=repr)))
    if isinstance(value, (list, tuple)):
        return ("sequence", tuple(_normalize(item) for item in value))
    if isinstance(value, (set, frozenset)):
        normalized = (_normalize(item) for item in value)
        return ("set", tuple(sorted(normalized, key=repr)))
    return value


def fingerprint(
    tool: str, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> tuple[Any, ...]:
    return (tool, _normalize(args), _normalize(kwargs))


class Execution:
    def __init__(self, limits: Limits):
        self.limits = limits
        self.started_at = monotonic()
        self.deadline = self.started_at + limits.timeout_seconds
        self.iterations = 0
        self.tool_calls = 0
        self.fingerprints: deque[tuple[Any, ...]] = deque(
            maxlen=max(limits.repeat_threshold, 1)
        )
        self.status: Status | None = None
        self.reason: Reason | None = None
        self._last_fingerprint: tuple[Any, ...] | None = None
        self._repeats = 0

    @property
    def terminal(self) -> bool:
        return self.status is not None

    def begin_iteration(self) -> None:
        self._ensure_active()
        if self.iterations >= self.limits.max_iterations:
            raise LimitTrip(
                Reason.ITERATION_LIMIT,
                f"iteration limit of {self.limits.max_iterations} reached",
            )
        self.iterations += 1

    async def call_tool(
        self,
        name: str,
        tool: AsyncTool[T],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> T:
        self._ensure_active()

        if self.tool_calls >= self.limits.max_tool_calls:
            raise LimitTrip(
                Reason.TOOL_BUDGET,
                f"tool budget of {self.limits.max_tool_calls} reached",
            )

        signature = fingerprint(name, args, kwargs)
        if signature == self._last_fingerprint:
            self._repeats += 1
        else:
            self._last_fingerprint = signature
            self._repeats = 1
        self.fingerprints.append(signature)

        if self._repeats >= self.limits.repeat_threshold:
            raise LimitTrip(
                Reason.LOOP_DETECTED,
                f"{name!r} called {self._repeats} times with identical inputs",
            )

        self.tool_calls += 1
        return await tool(*args, **kwargs)

    def terminate(self, status: Status, reason: Reason) -> None:
        if self.terminal:
            raise ExecutionClosed(
                f"execution already terminated: {self.status.value}"
            )
        self.status = status
        self.reason = reason

    def _ensure_active(self) -> None:
        if self.terminal:
            raise ExecutionClosed(
                f"execution already terminated: {self.status.value}"
            )


class Tripwire:
    def __init__(self, limits: Limits | None = None):
        self.limits = limits or Limits()

    async def execute(self, workflow: Workflow) -> ExecutionResult:
        execution = Execution(self.limits)
        task = asyncio.create_task(workflow(execution))

        try:
            done, _ = await asyncio.wait(
                {task}, timeout=self.limits.timeout_seconds
            )
        except asyncio.CancelledError:
            await _cancel(task)
            raise

        if not done:
            await _cancel(task)
            return self._result(
                execution,
                Status.TRIPPED,
                Reason.TIMEOUT,
                detail="execution deadline expired",
            )

        if task.cancelled():
            return self._result(
                execution, Status.CANCELLED, Reason.CANCELLED
            )

        error = task.exception()
        if error is None:
            return self._result(
                execution, Status.COMPLETED, Reason.COMPLETED
            )
        if isinstance(error, LimitTrip):
            return self._result(
                execution,
                Status.TRIPPED,
                error.reason,
                detail=error.detail,
            )
        if isinstance(error, Exception):
            return self._result(
                execution,
                Status.FAILED,
                Reason.ERROR,
                error=f"{type(error).__name__}: {error}",
            )
        raise error

    def _result(
        self,
        execution: Execution,
        status: Status,
        reason: Reason,
        error: str | None = None,
        detail: str | None = None,
    ) -> ExecutionResult:
        execution.terminate(status, reason)
        return ExecutionResult(
            status=status,
            reason=reason,
            iterations=execution.iterations,
            tool_calls=execution.tool_calls,
            elapsed_seconds=monotonic() - execution.started_at,
            error=error,
            detail=detail,
        )


async def _cancel(task: asyncio.Task[Any]) -> None:
    if not task.done():
        task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except LimitTrip:
        pass
    except Exception:
        pass
