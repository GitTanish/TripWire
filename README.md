# Tripwire

I'm exploring how to make agent execution more reliable.

Tripwire is a small async circuit breaker for agentic workflows. It wraps a workflow, enforces a few hard boundaries, and returns a structured result for every run. When a boundary is crossed, the workflow stops. No warning and no extra turn.

The failure mode I care about is not a wrong answer. It's unbounded execution. A model that can call tools in a loop can call the same tool forever. Tripwire makes that failure small, visible, and final.

Python, asyncio, deterministic tests, and a few focused experiments.

Work in progress. State is in memory, single-process, and cancellation is cooperative.

## What it enforces

Limits are checked before work begins.

- **Iterations** (`max_iterations`, default 100). An iteration limit of 10 allows 10 iterations. The eleventh is rejected before its body runs.
- **Tool calls** (`max_tool_calls`, default 50). A budget of two allows two calls. The third is stopped before invocation.
- **Time** (`timeout_seconds`, default 60). The deadline uses `monotonic()`. When it expires, Tripwire cancels the workflow task, waits for cleanup, and records a timeout.
- **Repeats** (`repeat_threshold`, default 3). Consecutive calls to the same tool with equivalent inputs trip the breaker. The third identical call is blocked before it executes, so a threshold of three means two calls actually run.

Inputs are normalized before comparison, so dict key order, nested dicts, keyword argument order, lists, tuples, and sets don't hide a repeat. A different tool or different inputs reset the count.

## Using it

```python
import asyncio

from tripwire import Limits, Tripwire


async def search(query: str) -> str:
    await asyncio.sleep(0)
    return query


async def runaway_agent(execution) -> None:
    while True:
        execution.begin_iteration()
        await execution.call_tool("search", search, "the same query")


async def main() -> None:
    tripwire = Tripwire(
        Limits(
            max_iterations=100,
            max_tool_calls=10,
            timeout_seconds=5.0,
            repeat_threshold=3,
        )
    )

    result = await tripwire.execute(runaway_agent)
    print(f"{result.status.value}: {result.reason.value}")


asyncio.run(main())
```

The same workflow lives in `examples/runaway_agent.py`:

```
tripped: loop_detected after 3 iterations and 2 tool calls
```

## The result

Every execution ends with an `ExecutionResult`, whether it completed or not. Expected limit violations are outcomes, not exceptions.

```python
@dataclass(frozen=True)
class ExecutionResult:
    status: Status
    reason: Reason
    iterations: int
    tool_calls: int
    elapsed_seconds: float
    error: str | None = None
    detail: str | None = None
```

`status` is one of `completed`, `tripped`, `failed`, or `cancelled`.

- Limit violations are `tripped`, with a reason of `iteration_limit`, `tool_budget`, `loop_detected`, or `timeout`.
- Unexpected exceptions are `failed`, with the exception type and message in `error`.
- A workflow that cancels itself is `cancelled`. Cancelling `execute` from outside still raises `CancelledError` after the child task is cleaned up.
- `result.ok` is true only for `completed`.

## State

Each execution owns its counters, start time, deadline, recent fingerprints, and terminal status. Run the same `Tripwire` twice and the runs are independent.

Once an execution is terminal, `begin_iteration()` and `call_tool()` raise `ExecutionClosed`. There is no silent reset, even if a coroutine wakes up late.

The internal `LimitTrip` inherits from `BaseException`, not `Exception`, so a workflow's generic `except Exception` cannot swallow a limit violation.

## Tests

The suite is the specification. `pytest` runs 28 tests covering zero limits, exact boundaries, repeat thresholds, input normalization, deadline cancellation, cleanup propagation, failure paths, terminal behavior, isolated state, and return values.

Real time appears only in the timeout tests, and those assert cancellation and cleanup rather than precise durations. Current status: 28 passing.

The interesting question is still the boundary. Tripwire owns limit enforcement, state tracking, and result generation. The workflow owns its loop, and the runner owns scheduling and cancellation. Changes should follow evidence from implementation and testing.
