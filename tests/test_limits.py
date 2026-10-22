import asyncio

from tripwire import Limits, Reason, Status, Tripwire, fingerprint


def run(workflow, limits: Limits):
    return asyncio.run(Tripwire(limits).execute(workflow))


def test_tool_budget_cannot_be_exceeded():
    calls = []

    async def search(query):
        calls.append(query)
        return query

    async def workflow(execution):
        execution.begin_iteration()
        await execution.call_tool("search", search, "a")
        await execution.call_tool("search", search, "b")
        await execution.call_tool("search", search, "c")

    result = run(workflow, Limits(max_tool_calls=2))

    assert calls == ["a", "b"]
    assert result.tool_calls == 2
    assert result.status is Status.TRIPPED
    assert result.reason is Reason.TOOL_BUDGET


def test_three_identical_calls_trip_breaker():
    calls = []

    async def search(query):
        calls.append(query)
        return query

    async def workflow(execution):
        for _ in range(3):
            await execution.call_tool("search", search, "same")

    result = run(
        workflow, Limits(max_tool_calls=10, repeat_threshold=3)
    )

    assert calls == ["same", "same"]
    assert result.tool_calls == 2
    assert result.status is Status.TRIPPED
    assert result.reason is Reason.LOOP_DETECTED
    assert "3 times" in result.detail


def test_equivalent_dictionary_inputs_share_fingerprint():
    calls = []

    async def search(payload):
        calls.append(payload)

    async def workflow(execution):
        await execution.call_tool("search", search, {"query": "x", "limit": 5})
        await execution.call_tool("search", search, {"limit": 5, "query": "x"})
        await execution.call_tool("search", search, {"query": "x", "limit": 5})

    result = run(
        workflow, Limits(max_tool_calls=10, repeat_threshold=3)
    )

    assert len(calls) == 2
    assert result.status is Status.TRIPPED
    assert result.reason is Reason.LOOP_DETECTED


def test_different_inputs_do_not_trip_breaker():
    async def search(payload):
        return payload

    async def workflow(execution):
        await execution.call_tool("search", search, {"query": "a"})
        await execution.call_tool("search", search, {"query": "a"})
        await execution.call_tool("search", search, {"query": "b"})
        await execution.call_tool("search", search, {"query": "a"})
        await execution.call_tool("search", search, {"query": "a"})

    result = run(
        workflow, Limits(max_tool_calls=10, repeat_threshold=3)
    )

    assert result.status is Status.COMPLETED
    assert result.tool_calls == 5


def test_different_tool_resets_repeat_count():
    async def search(payload):
        return payload

    async def workflow(execution):
        await execution.call_tool("search", search, {"query": "a"})
        await execution.call_tool("search", search, {"query": "a"})
        await execution.call_tool("other", search, {"query": "a"})
        await execution.call_tool("other", search, {"query": "a"})

    result = run(
        workflow, Limits(max_tool_calls=10, repeat_threshold=3)
    )

    assert result.status is Status.COMPLETED
    assert result.tool_calls == 4


def test_time_budget_trips_after_expiry():
    started = []
    cleaned_up = []

    async def workflow(execution):
        execution.begin_iteration()
        started.append(True)
        try:
            await asyncio.sleep(10)
        finally:
            cleaned_up.append(True)

    result = run(workflow, Limits(timeout_seconds=0.05))

    assert started == [True]
    assert cleaned_up == [True]
    assert result.status is Status.TRIPPED
    assert result.reason is Reason.TIMEOUT
    assert result.iterations == 1
    assert result.tool_calls == 0
    assert result.detail == "execution deadline expired"


def test_tool_error_fails_execution():
    async def broken_tool():
        raise RuntimeError("tool exploded")

    async def workflow(execution):
        await execution.call_tool("broken", broken_tool)

    result = run(workflow, Limits())

    assert result.status is Status.FAILED
    assert result.reason is Reason.ERROR
    assert result.error == "RuntimeError: tool exploded"
    assert result.tool_calls == 1


def test_zero_tool_budget_prevents_first_call():
    calls = []

    async def tool():
        calls.append(True)

    async def workflow(execution):
        execution.begin_iteration()
        await execution.call_tool("tool", tool)

    result = run(workflow, Limits(max_tool_calls=0))

    assert calls == []
    assert result.tool_calls == 0
    assert result.status is Status.TRIPPED
    assert result.reason is Reason.TOOL_BUDGET
    assert result.detail == "tool budget of 0 reached"


def test_repeat_threshold_boundary():
    calls = []

    async def tool():
        calls.append(True)

    async def workflow(execution):
        for _ in range(4):
            await execution.call_tool("tool", tool)

    result = run(workflow, Limits(max_tool_calls=10, repeat_threshold=4))

    assert len(calls) == 3
    assert result.tool_calls == 3
    assert result.status is Status.TRIPPED
    assert result.reason is Reason.LOOP_DETECTED
    assert "4 times" in result.detail


def test_identical_inputs_to_different_tools_do_not_trip():
    async def tool(value):
        return value

    async def workflow(execution):
        await execution.call_tool("a", tool, "x")
        await execution.call_tool("a", tool, "x")
        await execution.call_tool("b", tool, "x")
        await execution.call_tool("b", tool, "x")

    result = run(
        workflow, Limits(max_tool_calls=10, repeat_threshold=3)
    )

    assert result.status is Status.COMPLETED
    assert result.tool_calls == 4


def test_keyword_argument_order_is_normalized():
    calls = []

    async def search(**kwargs):
        calls.append(kwargs)

    async def workflow(execution):
        await execution.call_tool("search", search, query="x", limit=5)
        await execution.call_tool("search", search, limit=5, query="x")
        await execution.call_tool("search", search, query="x", limit=5)

    result = run(
        workflow, Limits(max_tool_calls=10, repeat_threshold=3)
    )

    assert len(calls) == 2
    assert result.status is Status.TRIPPED
    assert result.reason is Reason.LOOP_DETECTED


def test_nested_dictionary_inputs_are_normalized():
    async def search(payload):
        return payload

    async def workflow(execution):
        await execution.call_tool(
            "search", search, {"filter": {"b": 2, "a": 1}, "query": "x"}
        )
        await execution.call_tool(
            "search", search, {"query": "x", "filter": {"a": 1, "b": 2}}
        )
        await execution.call_tool(
            "search", search, {"filter": {"a": 1, "b": 2}, "query": "x"}
        )

    result = run(
        workflow, Limits(max_tool_calls=10, repeat_threshold=3)
    )

    assert result.status is Status.TRIPPED
    assert result.reason is Reason.LOOP_DETECTED
    assert result.tool_calls == 2


def test_timeout_preserves_progress_counts():
    async def tool():
        return "ok"

    async def workflow(execution):
        execution.begin_iteration()
        execution.begin_iteration()
        await execution.call_tool("tool", tool)
        await asyncio.sleep(10)

    result = run(workflow, Limits(timeout_seconds=0.1))

    assert result.status is Status.TRIPPED
    assert result.reason is Reason.TIMEOUT
    assert result.iterations == 2
    assert result.tool_calls == 1


def test_state_records_recent_fingerprints():
    handles = []

    async def tool(value):
        return value

    async def workflow(execution):
        handles.append(execution)
        await execution.call_tool("a", tool, "x")
        await execution.call_tool("b", tool, "y")

    result = run(workflow, Limits(repeat_threshold=3))
    execution = handles[0]

    assert result.status is Status.COMPLETED
    assert len(execution.fingerprints) == 2
    assert execution.fingerprints[0] == fingerprint("a", ("x",), {})
    assert execution.fingerprints[1] == fingerprint("b", ("y",), {})
    assert execution.fingerprints[0] != execution.fingerprints[1]
