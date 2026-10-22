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

    print(
        f"{result.status.value}: {result.reason.value} "
        f"after {result.iterations} iterations and {result.tool_calls} tool calls"
    )


if __name__ == "__main__":
    asyncio.run(main())
