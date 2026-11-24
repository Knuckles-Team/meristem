"""Compile the context from the engine's selected partition
(MERISTEM-HARNESS-CYCLE-R005.1)."""

from __future__ import annotations

import ports_support as support
import pytest
from pydantic_ai.models.test import TestModel
from support.recorded_engine import RecordedEnginePort, params_digest

from meristem.graph import HarnessConfig, RunPlan, create_graph_agent, execute_graph
from meristem.harness import plan_runtime, run_lifecycle

SPEC = "MERISTEM-HARNESS-CYCLE-R005.1"
# spec: MERISTEM-HARNESS-CYCLE-R005.1

MESSAGES = {"a": "content-a", "b": "content-b"}


async def _run_before_step(hook, *, port: RecordedEnginePort | None) -> dict:
    lifecycle = run_lifecycle.RunLifecycle(consumers=("context_partition",))
    lifecycle.add("context_partition", "before_step", hook)
    plan = RunPlan(
        model=TestModel(custom_output_text="done"),
        steps=("q",),
        lifecycle=lifecycle,
        harness=HarnessConfig(consumers=("context_partition",)),
    )
    support.register_run_decision_provider(lambda request: plan)
    if port is not None:
        support.register_engine_decision_port(port)
    graph, config = create_graph_agent(agent_model="m", ports=support.PORTS)
    return await execute_graph(graph, config, "q", run_id="r1", ports=support.PORTS)


@pytest.mark.spec(SPEC)
async def test_context_uses_selected_partition_only():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "context_budget",
                    "params": {"sources": ["a", "b"]},
                    "answer": {
                        "outcome": "solved",
                        "payload": {"selected": ["a"], "omitted": ["b"]},
                        "record_digest": "rec-1",
                    },
                }
            ]
        }
    )
    compiled: dict = {}

    async def partition(_state) -> None:
        compiled["result"] = await plan_runtime.compile_partition(
            MESSAGES, ("a", "b"), support.PORTS.engine_decision_port(), run_ref="r1"
        )

    result = await _run_before_step(partition, port=port)

    assert result["status"] == "completed"
    assert port.calls == [("context_budget", params_digest({"sources": ["a", "b"]}))]
    partition_result = compiled["result"]
    assert partition_result.selected == ("a",)
    assert partition_result.omitted == ("b",)
    # Only the selected content is kept, each source id as its citation.
    assert partition_result.content == (("a", "content-a"),)
    assert partition_result.record_digest == "rec-1"


@pytest.mark.spec(SPEC)
async def test_source_in_both_lists_refuses_partition_incomplete():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "context_budget",
                    "params": {"sources": ["a", "b"]},
                    "answer": {
                        "outcome": "solved",
                        "payload": {"selected": ["a"], "omitted": ["a"]},
                    },
                }
            ]
        }
    )

    async def partition(_state) -> None:
        await plan_runtime.compile_partition(
            MESSAGES, ("a", "b"), support.PORTS.engine_decision_port(), run_ref="r1"
        )

    result = await _run_before_step(partition, port=port)

    assert result["status"] == "failed"
    assert "partition_incomplete" in (result["error"] or "")
    assert port.calls == [("context_budget", params_digest({"sources": ["a", "b"]}))]


@pytest.mark.spec(SPEC)
async def test_source_in_neither_list_refuses_partition_incomplete():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "context_budget",
                    "params": {"sources": ["a", "b"]},
                    "answer": {
                        "outcome": "solved",
                        "payload": {"selected": ["a"], "omitted": []},
                    },
                }
            ]
        }
    )

    async def partition(_state) -> None:
        await plan_runtime.compile_partition(
            MESSAGES, ("a", "b"), support.PORTS.engine_decision_port(), run_ref="r1"
        )

    result = await _run_before_step(partition, port=port)

    assert result["status"] == "failed"
    assert "partition_incomplete" in (result["error"] or "")


@pytest.mark.spec(SPEC)
async def test_unbound_port_refuses_and_decides_nothing():
    async def partition(_state) -> None:
        await plan_runtime.compile_partition(
            MESSAGES, ("a", "b"), support.PORTS.engine_decision_port(), run_ref="r1"
        )

    result = await _run_before_step(partition, port=None)

    assert result["status"] == "failed"
    assert "engine_decision_unavailable" in (result["error"] or "")
