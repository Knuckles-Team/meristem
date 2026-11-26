"""Each model step reports its cost facts as a usage event (R003.1)."""

from __future__ import annotations

import json

import ports_support as support
import pytest
from pydantic_ai.models.test import TestModel

from meristem.graph import HarnessConfig, RunPlan, create_graph_agent, execute_graph


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R003.1")
async def test_step_usage_event_carries_cost_and_digest(events):
    plan = RunPlan(
        model=TestModel(custom_output_text="done"),
        steps=("q",),
        harness=HarnessConfig(consumers=("step_usage",)),
    )
    support.register_run_decision_provider(lambda request: plan)
    graph, config = create_graph_agent(agent_model="m", ports=support.PORTS)
    result = await execute_graph(graph, config, "q", run_id="r1", ports=support.PORTS)
    usage = [e for e in events if e.get("type") == "step_usage"]
    assert (
        result["status"] == "completed"
        and len(usage) == result["metrics"]["observed_model_requests"]
    )
    event = usage[0]
    assert event["run_id"] == "r1" and event["outcome"] == "ok"
    assert len(event["content_digest"]) == 64 and int(event["content_digest"], 16) >= 0
    assert {
        "tokens_in",
        "tokens_out",
        "tokens_cached",
        "latency_ms",
        "model_ref",
    } <= set(event)
    assert "done" not in json.dumps(event)


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R003.1")
async def test_unlisted_consumer_emits_nothing(events):
    plan = RunPlan(model=TestModel(custom_output_text="done"), steps=("q",))
    support.register_run_decision_provider(lambda request: plan)
    graph, config = create_graph_agent(agent_model="m", ports=support.PORTS)
    await execute_graph(graph, config, "q", run_id="r1", ports=support.PORTS)
    assert not [e for e in events if e.get("type") == "step_usage"]
