"""Ask the engine for topology (MERISTEM-HARNESS-CYCLE-R012.1)."""

from __future__ import annotations

import inspect

import ports_support as support
import pytest
from pydantic_ai.models.test import TestModel
from support.recorded_engine import RecordedEnginePort

from meristem.graph import (
    HarnessConfig,
    RunPlan,
    budget,
    create_graph_agent,
    execute_graph,
)
from meristem.graph.state import RunBudget, StopRule
from meristem.harness import run_lifecycle

SPEC = "MERISTEM-HARNESS-CYCLE-R012.1"


def _params(capacity: int) -> dict:
    return {"task_class": None, "capacity": capacity}


def _plan() -> RunPlan:
    lifecycle = run_lifecycle.RunLifecycle(consumers=("topology",))
    return RunPlan(
        model=TestModel(custom_output_text="done"),
        steps=("q",),
        budget=RunBudget(max_concurrent_requests=2),
        stop=StopRule(max_steps=5),
        lifecycle=lifecycle,
        harness=HarnessConfig(consumers=("topology",)),
    )


async def _run_on_start(
    hook, *, port: RecordedEnginePort | None
) -> tuple[dict, RunPlan]:
    """One run whose admitted ``on_start`` consumer is ``hook``."""
    plan = _plan()
    plan.lifecycle.add("topology", "on_start", hook)
    support.register_run_decision_provider(lambda request: plan)
    if port is not None:
        support.register_engine_decision_port(port)
    graph, config = create_graph_agent(agent_model="m", ports=support.PORTS)
    result = await execute_graph(graph, config, "q", run_id="r1", ports=support.PORTS)
    return result, plan


@pytest.mark.spec(SPEC)
async def test_topology_comes_from_decide():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "topology",
                    "params": _params(2),
                    "answer": {
                        "outcome": "committed",
                        "payload": {"width": 2, "depth": 2, "rounds": 4},
                        "record_digest": "topo-1",
                    },
                }
            ]
        }
    )
    applied: dict = {}

    async def topology(request, plan) -> None:
        applied["plan"] = await budget.apply_topology(
            request, plan, support.PORTS.engine_decision_port()
        )

    result, _ = await _run_on_start(topology, port=port)

    assert result["status"] == "completed"
    assert [method for method, _ in port.calls] == ["topology"]
    updated = applied["plan"]
    assert updated.budget.max_concurrent_requests == 2
    assert updated.stop.max_steps == 4
    # The harness keeps no success-rate estimate and no outcome average.
    source = inspect.getsource(budget)
    assert "success_rate" not in source
    assert "outcome_average" not in source


@pytest.mark.spec(SPEC)
async def test_answer_above_the_host_ceiling_refuses_topology_not_applicable():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "topology",
                    "params": _params(2),
                    "answer": {
                        "outcome": "committed",
                        "payload": {"width": 3, "depth": 1, "rounds": 2},
                    },
                }
            ]
        }
    )
    applied: dict = {}

    async def topology(request, plan) -> None:
        applied["plan"] = await budget.apply_topology(
            request, plan, support.PORTS.engine_decision_port()
        )

    result, _ = await _run_on_start(topology, port=port)

    assert result["status"] == "failed"
    assert "topology_not_applicable" in (result["error"] or "")
    assert "plan" not in applied


@pytest.mark.spec(SPEC)
async def test_unbound_port_refuses_and_decides_nothing():
    applied: dict = {}

    async def topology(request, plan) -> None:
        applied["plan"] = await budget.apply_topology(
            request, plan, support.PORTS.engine_decision_port()
        )

    result, _ = await _run_on_start(topology, port=None)

    assert result["status"] == "failed"
    assert "engine_decision_unavailable" in (result["error"] or "")
    assert "plan" not in applied


@pytest.mark.spec(SPEC)
async def test_abstained_answer_decides_nothing():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "topology",
                    "params": _params(2),
                    "answer": {"outcome": "abstained"},
                }
            ]
        }
    )
    applied: dict = {}

    async def topology(request, plan) -> None:
        applied["plan"] = await budget.apply_topology(
            request, plan, support.PORTS.engine_decision_port()
        )

    result, plan = await _run_on_start(topology, port=port)

    assert result["status"] == "completed"
    assert applied["plan"] is plan
