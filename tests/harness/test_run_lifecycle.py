"""Run lifecycle hooks called from the run loop (MERISTEM-HARNESS-CYCLE-R078)."""

from __future__ import annotations

import ports_support as support
import pytest
from pydantic_ai.models.test import TestModel

from meristem.graph import HarnessConfig, RunPlan, create_graph_agent, execute_graph
from meristem.harness import run_lifecycle


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R078")
async def test_execute_graph_calls_each_hook_once_in_order():
    calls: list[str] = []
    admitted = tuple(f"consumer-{point}" for point in run_lifecycle.POINTS)

    # Run 1: every admitted consumer runs once, in point order; an unlisted
    # consumer never runs.
    lifecycle = run_lifecycle.RunLifecycle(consumers=admitted)
    for point in run_lifecycle.POINTS:

        async def step(*_args, _point=point):
            calls.append(_point)

        async def ghost(*_args, _point=point):
            calls.append(f"ghost:{_point}")

        lifecycle.add(f"consumer-{point}", point, step)
        lifecycle.add("ghost", point, ghost)

    graph, config = create_graph_agent(agent_model="m", ports=support.PORTS)
    plan = RunPlan(
        model=TestModel(custom_output_text="done"),
        steps=("q",),
        harness=HarnessConfig(consumers=admitted),
        lifecycle=lifecycle,
    )
    support.register_run_decision_provider(lambda request: plan)
    result = await execute_graph(graph, config, "q", run_id="r1", ports=support.PORTS)
    assert result["status"] == "completed"
    assert calls == list(run_lifecycle.POINTS)
    assert not any(name.startswith("ghost") for name in calls)

    # Run 2: without a lifecycle on the plan the loop builds the default one
    # and no consumer step runs at all.
    support.register_run_decision_provider(
        lambda request: RunPlan(
            model=TestModel(custom_output_text="plain"), steps=("q",)
        )
    )
    result = await execute_graph(graph, config, "q", run_id="r2", ports=support.PORTS)
    assert (result["status"], calls) == ("completed", list(run_lifecycle.POINTS))

    # Run 3: a consumer refusal ends the run failed closed with its code.
    calls.clear()
    refusing = run_lifecycle.RunLifecycle(consumers=admitted + ("refuser",))
    for point in run_lifecycle.POINTS:

        async def step2(*_args, _point=point):
            calls.append(_point)

        refusing.add(f"consumer-{point}", point, step2)

    async def refuse(_state, _step):
        raise run_lifecycle.ConsumerRefused("consumer_refused_code")

    refusing.add("refuser", "after_step", refuse)
    support.register_run_decision_provider(
        lambda request: RunPlan(
            model=TestModel(custom_output_text="done"),
            steps=("q",),
            harness=HarnessConfig(consumers=admitted + ("refuser",)),
            lifecycle=refusing,
        )
    )
    result = await execute_graph(graph, config, "q", run_id="r3", ports=support.PORTS)
    assert result["status"] == "failed"
    assert "ConsumerRefused" in (result["error"] or "")
    assert "consumer_refused_code" in (result["error"] or "")
    assert calls == ["on_start", "before_step", "after_step", "on_end"]
