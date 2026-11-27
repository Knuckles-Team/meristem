"""The installed constrained path keeps one terminal outcome and finite limits."""

import asyncio
from dataclasses import replace

import ports_support as support
import pytest
from test_text_only import fixture

from meristem.graph import AgentRunExecutor
from meristem.graph.executor import iterate_run
from meristem.graph.state import HarnessConfig

TERMINAL = {"graph_complete", "run_cancelled", "error"}


@pytest.mark.spec("MERISTEM-RUN-R001.13.2")
@pytest.mark.asyncio
async def test_cancel_then_restart_keeps_one_terminal_outcome():
    entered, exited = asyncio.Event(), asyncio.Event()

    async def blocked(request):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            exited.set()

    client, _, plan = fixture(handler_override=blocked)
    seen: list[str] = []

    async def consume():
        async for event in iterate_run(support.request(task="hello"), plan=plan):
            seen.append(event["type"])

    task = asyncio.create_task(consume())
    try:
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        await client.close()
    # The provider drained and the cancelled run yielded no terminal result.
    assert exited.is_set() and not set(seen) & TERMINAL

    # A restarted host serves the next request on a fresh executor with no carry-over.
    client, calls, plan = fixture()
    try:
        restarted = [
            e["type"]
            async for e in iterate_run(support.request(task="hello"), plan=plan)
        ]
        assert [t for t in restarted if t in TERMINAL] == ["graph_complete"]
        assert len(calls) == 1
    finally:
        await client.close()


@pytest.mark.spec("MERISTEM-RUN-R001.13.2")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "settings",
    [None, {}, {"max_tokens": 0}, {"max_tokens": 21}, {"max_tokens": True}],
)
async def test_output_cap_is_required_and_finite(settings):
    client, calls, plan = fixture()
    plan = replace(plan, harness=HarnessConfig(retries=0, model_settings=settings))
    try:
        with pytest.raises(ValueError, match="text-only execution is unavailable"):
            await AgentRunExecutor(ports=support.PORTS).execute_text_only(
                support.request(task="hello"), plan=plan
            )
        assert calls == []
    finally:
        await client.close()
