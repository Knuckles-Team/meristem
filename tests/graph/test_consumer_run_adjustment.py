"""Adjust the next run from observed cost (MERISTEM-HARNESS-CYCLE-R019.1)."""

from __future__ import annotations

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
from meristem.graph.state import RunBudget
from meristem.harness import run_lifecycle

SPEC = "MERISTEM-HARNESS-CYCLE-R019.1"


def _params(current_width: int, budget_tokens: int | None) -> dict:
    return {
        "child_id": None,
        "operation": None,
        "current_model": None,
        "current_width": current_width,
        "host_width_ceiling": current_width,
        "budget_tokens": budget_tokens,
    }


def _plan() -> RunPlan:
    lifecycle = run_lifecycle.RunLifecycle(consumers=("run_adjustment",))
    return RunPlan(
        model=TestModel(custom_output_text="done"),
        steps=("q",),
        budget=RunBudget(max_concurrent_requests=2, total_tokens_limit=100),
        lifecycle=lifecycle,
        harness=HarnessConfig(consumers=("run_adjustment",)),
    )


async def _run_on_start(
    hook, *, port: RecordedEnginePort | None
) -> tuple[dict, RunPlan]:
    """One run whose admitted ``on_start`` consumer is ``hook``."""
    plan = _plan()
    plan.lifecycle.add("run_adjustment", "on_start", hook)
    support.register_run_decision_provider(lambda request: plan)
    if port is not None:
        support.register_engine_decision_port(port)
    graph, config = create_graph_agent(agent_model="m", ports=support.PORTS)
    result = await execute_graph(graph, config, "q", run_id="r1", ports=support.PORTS)
    return result, plan


@pytest.mark.spec(SPEC)
async def test_next_run_respects_host_ceiling():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "run_adjustment",
                    "params": _params(2, 100),
                    "answer": {
                        "outcome": "solved",
                        "option_ids": ("model:m-2", "width:2", "budget_tokens:80"),
                        "record_digest": "adj-1",
                    },
                }
            ]
        }
    )
    applied: dict = {}

    async def adjust(request, plan) -> None:
        applied["plan"] = await budget.apply_run_adjustment(
            request, plan, support.PORTS.engine_decision_port()
        )

    result, _ = await _run_on_start(adjust, port=port)

    assert result["status"] == "completed"
    assert [method for method, _ in port.calls] == ["run_adjustment"]
    updated = applied["plan"]
    # The received options apply within the host ceiling.
    assert updated.model == "m-2"
    assert updated.budget.max_concurrent_requests == 2
    assert updated.budget.total_tokens_limit == 80


@pytest.mark.spec(SPEC)
async def test_width_above_the_host_ceiling_refuses():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "run_adjustment",
                    "params": _params(2, 100),
                    "answer": {
                        "outcome": "solved",
                        "option_ids": ("width:3",),
                    },
                }
            ]
        }
    )
    applied: dict = {}

    async def adjust(request, plan) -> None:
        applied["plan"] = await budget.apply_run_adjustment(
            request, plan, support.PORTS.engine_decision_port()
        )

    result, _ = await _run_on_start(adjust, port=port)

    assert result["status"] == "failed"
    assert "width_above_host_ceiling" in (result["error"] or "")
    assert "plan" not in applied


@pytest.mark.spec(SPEC)
async def test_unbound_port_refuses_and_decides_nothing():
    applied: dict = {}

    async def adjust(request, plan) -> None:
        applied["plan"] = await budget.apply_run_adjustment(
            request, plan, support.PORTS.engine_decision_port()
        )

    result, _ = await _run_on_start(adjust, port=None)

    assert result["status"] == "failed"
    assert "engine_decision_unavailable" in (result["error"] or "")
    assert "plan" not in applied


@pytest.mark.spec(SPEC)
async def test_abstained_answer_keeps_the_plan():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "run_adjustment",
                    "params": _params(2, 100),
                    "answer": {"outcome": "abstained"},
                }
            ]
        }
    )
    applied: dict = {}

    async def adjust(request, plan) -> None:
        applied["plan"] = await budget.apply_run_adjustment(
            request, plan, support.PORTS.engine_decision_port()
        )

    result, plan = await _run_on_start(adjust, port=port)

    assert result["status"] == "completed"
    assert applied["plan"] is plan
