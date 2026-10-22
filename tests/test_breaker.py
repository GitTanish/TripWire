import asyncio

import pytest

from tripwire import (
    ExecutionClosed,
    Limits,
    Reason,
    Status,
    Tripwire,
)


def run(workflow, limits: Limits):
    return asyncio.run(Tripwire(limits).execute(workflow))


def test_normal_execution_completes_without_tripping():
    async def workflow(execution):
        execution.begin_iteration()
        execution.begin_iteration()

    result = run(workflow, Limits(max_iterations=2))

    assert result.status is Status.COMPLETED
    assert result.reason is Reason.COMPLETED
    assert result.iterations == 2
    assert result.tool_calls == 0
    assert result.elapsed_seconds >= 0
    assert result.error is None
    assert result.detail is None
    assert result.ok


def test_iteration_limit_trips_at_exact_boundary():
    started = []

    async def workflow(execution):
        for index in range(10):
            execution.begin_iteration()
            started.append(index)
        execution.begin_iteration()
        started.append("eleventh")

    result = run(workflow, Limits(max_iterations=10))

    assert started == list(range(10))
    assert result.status is Status.TRIPPED
    assert result.reason is Reason.ITERATION_LIMIT
    assert result.iterations == 10
    assert result.detail == "iteration limit of 10 reached"


def test_zero_iteration_limit_prevents_first_iteration():
    started = []

    async def workflow(execution):
        execution.begin_iteration()
        started.append(True)

    result = run(workflow, Limits(max_iterations=0))

    assert started == []
    assert result.iterations == 0
    assert result.status is Status.TRIPPED
    assert result.reason is Reason.ITERATION_LIMIT
    assert result.detail == "iteration limit of 0 reached"


def test_state_matches_result_and_records_deadline():
    handles = []

    async def workflow(execution):
        handles.append(execution)
        execution.begin_iteration()
        execution.begin_iteration()

    result = run(workflow, Limits(max_iterations=2, timeout_seconds=5))
    execution = handles[0]

    assert execution.iterations == result.iterations == 2
    assert execution.tool_calls == result.tool_calls == 0
    assert execution.terminal
    assert execution.status is result.status
    assert execution.reason is result.reason
    assert execution.deadline == execution.started_at + 5
    assert execution.started_at <= execution.deadline


def test_completed_execution_cannot_continue():
    handles = []

    async def workflow(execution):
        handles.append(execution)
        execution.begin_iteration()

    result = run(workflow, Limits(max_iterations=1))
    execution = handles[0]

    assert result.status is Status.COMPLETED
    with pytest.raises(ExecutionClosed):
        execution.begin_iteration()
    assert execution.iterations == 1


def test_tripped_execution_cannot_resume():
    handles = []

    async def workflow(execution):
        handles.append(execution)
        execution.begin_iteration()
        execution.begin_iteration()

    result = run(workflow, Limits(max_iterations=1))
    execution = handles[0]

    assert result.status is Status.TRIPPED
    assert result.reason is Reason.ITERATION_LIMIT
    assert execution.iterations == 1

    with pytest.raises(ExecutionClosed):
        execution.begin_iteration()

    async def noop():
        return None

    with pytest.raises(ExecutionClosed):
        asyncio.run(execution.call_tool("noop", noop))

    assert execution.iterations == 1
    assert execution.tool_calls == 0


def test_timed_out_execution_cannot_resume():
    handles = []

    async def workflow(execution):
        handles.append(execution)
        execution.begin_iteration()
        await asyncio.sleep(10)

    result = run(workflow, Limits(timeout_seconds=0.05))
    execution = handles[0]

    assert result.status is Status.TRIPPED
    assert result.reason is Reason.TIMEOUT
    assert execution.terminal
    with pytest.raises(ExecutionClosed):
        execution.begin_iteration()
    assert execution.iterations == 1


def test_failed_execution_is_terminal():
    handles = []

    async def workflow(execution):
        handles.append(execution)
        raise RuntimeError("boom")

    result = run(workflow, Limits())
    execution = handles[0]

    assert result.status is Status.FAILED
    with pytest.raises(ExecutionClosed):
        execution.begin_iteration()


def test_cancelled_workflow_returns_cancelled_result():
    async def workflow(execution):
        execution.begin_iteration()
        raise asyncio.CancelledError

    result = run(workflow, Limits())

    assert result.status is Status.CANCELLED
    assert result.reason is Reason.CANCELLED
    assert result.iterations == 1
    assert not result.ok


def test_unexpected_error_is_visible_in_result():
    async def workflow(execution):
        raise ValueError("boom")

    result = run(workflow, Limits())

    assert result.status is Status.FAILED
    assert result.reason is Reason.ERROR
    assert result.error == "ValueError: boom"
    assert not result.ok


def test_failure_preserves_progress_counts():
    async def tool(value):
        return value

    async def workflow(execution):
        execution.begin_iteration()
        execution.begin_iteration()
        await execution.call_tool("tool", tool, "x")
        raise RuntimeError("boom")

    result = run(workflow, Limits())

    assert result.status is Status.FAILED
    assert result.iterations == 2
    assert result.tool_calls == 1
    assert result.error == "RuntimeError: boom"


def test_each_execution_has_isolated_state():
    async def scenario():
        tripwire = Tripwire(Limits(max_iterations=1))
        executions = []

        async def workflow(execution):
            executions.append(execution)
            execution.begin_iteration()

        first = await tripwire.execute(workflow)
        second = await tripwire.execute(workflow)
        return first, second, executions

    first, second, executions = asyncio.run(scenario())

    assert first.status is Status.COMPLETED
    assert second.status is Status.COMPLETED
    assert first.iterations == second.iterations == 1
    assert executions[0] is not executions[1]
    assert executions[0].started_at <= executions[1].started_at


def test_tool_return_value_passes_through():
    received = []

    async def search(query):
        return f"results:{query}"

    async def workflow(execution):
        value = await execution.call_tool("search", search, "x")
        received.append(value)

    result = run(workflow, Limits())

    assert received == ["results:x"]
    assert result.status is Status.COMPLETED
    assert result.tool_calls == 1


def test_external_cancellation_propagates():
    async def scenario():
        started = asyncio.Event()
        cleaned_up = []

        async def workflow(execution):
            started.set()
            try:
                await asyncio.sleep(10)
            finally:
                cleaned_up.append(True)

        tripwire = Tripwire(Limits(timeout_seconds=30))
        task = asyncio.create_task(tripwire.execute(workflow))
        await started.wait()
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        assert cleaned_up == [True]

    asyncio.run(scenario())
