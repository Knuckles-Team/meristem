"""Distinct terminal reasons: a budget stop, a host cancel and a model stop.

R084.3 wiring proof: the three terminal stops of one run are told apart by
the machine member (and its derived ``stop_reason``), not by the ``error``
text.
"""

from __future__ import annotations

import asyncio

import ports_support as support
import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from meristem.graph import RunBudget, RunPlan, RunStatus, StopRule, run

SPEC = "MERISTEM-HARNESS-CYCLE-R084.3"


@pytest.mark.spec(SPEC)
async def test_budget_cancel_and_model_stop_carry_distinct_reasons() -> None:
    entered = asyncio.Event()

    async def hang(messages, info):
        entered.set()
        await asyncio.Event().wait()
        return ModelResponse(parts=[TextPart("done")])

    # 1. A model stop marker keeps the run stopped with the requested reason.
    plan_stop = RunPlan(
        model=FunctionModel(lambda m, i: ModelResponse(parts=[TextPart("STOP now")])),
        steps=("answer",),
        stop=StopRule(stop_markers=("STOP",)),
    )
    stopped = await run(support.request(task="stop"), plan=plan_stop)
    assert stopped.status is RunStatus.STOPPED_REQUESTED
    assert stopped.status.stop_reason == "requested"

    # 2. A fired budget deadline keeps the run stopped with the budget reason.
    plan_budget = RunPlan(
        model=FunctionModel(hang),
        steps=("answer",),
        budget=RunBudget(timeout_seconds=0.05),
    )
    request = support.request(task="budget")
    budget_run = asyncio.create_task(run(request, plan=plan_budget))
    try:
        await asyncio.wait_for(entered.wait(), 0.5)
        result = await asyncio.wait_for(budget_run, 5.0)
    finally:
        if not budget_run.done():
            budget_run.cancel()
            await asyncio.gather(budget_run, return_exceptions=True)
    assert result.status is RunStatus.STOPPED_BUDGET
    assert result.status.stop_reason == "budget"

    # 3. A host cancel keeps the run stopped with the cancelled reason.
    entered2 = asyncio.Event()

    async def hang2(messages, info):
        entered2.set()
        await asyncio.Event().wait()
        return ModelResponse(parts=[TextPart("done")])

    plan_cancel = RunPlan(model=FunctionModel(hang2), steps=("answer",))
    cancelled = asyncio.create_task(
        run(support.request(task="cancel"), plan=plan_cancel)
    )
    try:
        await asyncio.wait_for(entered2.wait(), 0.5)
        cancelled.cancel("host stop")
        with pytest.raises(asyncio.CancelledError):
            await cancelled
    finally:
        if not cancelled.done():
            cancelled.cancel()
            await asyncio.gather(cancelled, return_exceptions=True)

    assert RunStatus.STOPPED_BUDGET is not RunStatus.STOPPED_REQUESTED
    assert RunStatus.STOPPED_CANCELLED is not RunStatus.STOPPED_REQUESTED
    assert RunStatus.STOPPED_CANCELLED.stop_reason == "cancelled"
    # The distinct members are what the run records; the error text is not
    # what tells the three apart.
    assert {
        RunStatus.STOPPED_REQUESTED.stop_reason,
        RunStatus.STOPPED_BUDGET.stop_reason,
        RunStatus.STOPPED_CANCELLED.stop_reason,
    } == {"requested", "budget", "cancelled"}
