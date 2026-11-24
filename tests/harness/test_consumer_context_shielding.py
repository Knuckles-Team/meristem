"""Shield workflow boundaries: the Decide request carries only the
params the boundary declares (MERISTEM-HARNESS-CYCLE-R006.1)."""

from __future__ import annotations

import ports_support as support
import pytest
from pydantic_ai.models.test import TestModel
from support.recorded_engine import RecordedEnginePort, params_digest

from meristem.graph import HarnessConfig, RunPlan, create_graph_agent, execute_graph
from meristem.harness import plan_runtime, run_lifecycle
from meristem.host_ports import EngineDecisionRefused

SPEC = "MERISTEM-HARNESS-CYCLE-R006.1"
# spec: MERISTEM-HARNESS-CYCLE-R006.1


async def _run_before_step(hook, *, port: RecordedEnginePort | None) -> dict:
    lifecycle = run_lifecycle.RunLifecycle(consumers=("boundary",))
    lifecycle.add("boundary", "before_step", hook)
    plan = RunPlan(
        model=TestModel(custom_output_text="done"),
        steps=("q",),
        lifecycle=lifecycle,
        harness=HarnessConfig(consumers=("boundary",)),
    )
    support.register_run_decision_provider(lambda request: plan)
    if port is not None:
        support.register_engine_decision_port(port)
    graph, config = create_graph_agent(agent_model="m", ports=support.PORTS)
    return await execute_graph(graph, config, "q", run_id="r1", ports=support.PORTS)


@pytest.mark.spec(SPEC)
async def test_boundary_gets_own_params_only():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "route",
                    "params": {"alpha": 1, "beta": 2},
                    "answer": {"outcome": "solved", "record_digest": "rec-1"},
                }
            ]
        }
    )
    request: dict = {}

    async def boundary(_state) -> None:
        built = plan_runtime.boundary_params({"alpha": 1, "beta": 2}, {"alpha", "beta"})
        request["request"] = built
        await support.PORTS.engine_decision_port().decide(
            "route", built.params, run_ref="r1"
        )

    result = await _run_before_step(boundary, port=port)

    assert result["status"] == "completed"
    # The sent param names are recorded sorted.
    assert request["request"].param_names == ("alpha", "beta")
    assert port.calls == [("route", params_digest({"alpha": 1, "beta": 2}))]


@pytest.mark.spec(SPEC)
async def test_undeclared_param_refuses_boundary_param_undeclared():
    port = RecordedEnginePort({"answers": []})

    async def boundary(_state) -> None:
        built = plan_runtime.boundary_params({"alpha": 1, "gamma": 3}, {"alpha"})
        await support.PORTS.engine_decision_port().decide(
            "route", built.params, run_ref="r1"
        )

    result = await _run_before_step(boundary, port=port)

    assert result["status"] == "failed"
    assert "boundary_param_undeclared" in (result["error"] or "")
    # The refusal happens before any engine question.
    assert port.calls == []


@pytest.mark.spec(SPEC)
async def test_unbound_port_refuses_and_decides_nothing():
    async def boundary(_state) -> None:
        port = support.PORTS.engine_decision_port()
        if port is None:
            raise EngineDecisionRefused("engine_decision_unavailable")
        built = plan_runtime.boundary_params({"alpha": 1}, {"alpha"})
        await port.decide("route", built.params, run_ref="r1")

    result = await _run_before_step(boundary, port=None)

    assert result["status"] == "failed"
    assert "engine_decision_unavailable" in (result["error"] or "")
