"""Submit observed insights at run end and record the injected ones
(MERISTEM-HARNESS-CYCLE-R022.2, MERISTEM-HARNESS-CYCLE-R022.3)."""

from __future__ import annotations

import hashlib
import json

import ports_support as support
import pytest
from pydantic_ai.models.test import TestModel
from support.recorded_engine import RecordedEnginePort, params_digest

from meristem.graph import HarnessConfig, RunPlan, create_graph_agent, execute_graph
from meristem.graph.state import GraphState
from meristem.harness import plan_runtime, run_lifecycle

SPEC_SUBMIT = "MERISTEM-HARNESS-CYCLE-R022.2"
# spec: MERISTEM-HARNESS-CYCLE-R022.2
SPEC_RECORD = "MERISTEM-HARNESS-CYCLE-R022.3"
# spec: MERISTEM-HARNESS-CYCLE-R022.3


def _digest(value: object) -> str:
    canonical = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _run_on_end(hook, *, port: RecordedEnginePort | None) -> dict:
    lifecycle = run_lifecycle.RunLifecycle(consumers=("insight_end",))
    lifecycle.add("insight_end", "on_end", hook)
    plan = RunPlan(
        model=TestModel(custom_output_text="done"),
        steps=("q",),
        lifecycle=lifecycle,
        harness=HarnessConfig(consumers=("insight_end",)),
    )
    support.register_run_decision_provider(lambda request: plan)
    if port is not None:
        support.register_engine_decision_port(port)
    graph, config = create_graph_agent(agent_model="m", ports=support.PORTS)
    return await execute_graph(graph, config, "q", run_id="r1", ports=support.PORTS)


@pytest.mark.spec(SPEC_SUBMIT)
async def test_insight_submitted_at_run_end():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "log",
                    "params": {"op": "RunRecord"},
                    "answer": {"outcome": "solved", "record_digest": "rr-1"},
                }
            ]
        }
    )
    submission: dict = {}

    async def end(state) -> None:
        state.insights = (
            {
                "insight_id": "i1",
                "source_run_ids": ("r9",),
                "context_signature": "sig-1",
            },
        )
        submission["result"] = await plan_runtime.submit_insight(
            state, support.PORTS.engine_decision_port(), run_ref="r1"
        )

    result = await _run_on_end(end, port=port)

    assert result["status"] == "completed"
    assert port.calls == [("log", params_digest({"op": "RunRecord"}))]
    (item,) = submission["result"]
    assert item.insight_id == "i1"
    assert item.context_signature == "sig-1"
    assert item.record_digest == "rr-1"


@pytest.mark.spec(SPEC_SUBMIT)
async def test_insight_without_source_refuses():
    port = RecordedEnginePort({"answers": []})

    async def end(state) -> None:
        state.insights = (
            {"insight_id": "i1", "source_run_ids": (), "context_signature": "s"},
        )
        await plan_runtime.submit_insight(
            state, support.PORTS.engine_decision_port(), run_ref="r1"
        )

    result = await _run_on_end(end, port=port)

    assert result["status"] == "completed"
    assert "insight_without_source" in (result["error"] or "")
    # The refusal happens before any engine write.
    assert port.calls == []


@pytest.mark.spec(SPEC_RECORD)
async def test_selected_insights_applied_in_answer_order():
    context = [{"text": "c1"}]
    signature = _digest([dict(item) for item in context])
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "insight_selection",
                    "params": {"context_signature": signature},
                    "answer": {
                        "outcome": "solved",
                        "payload": {
                            "selected": [
                                {"insight_id": "i1", "sources": ["r9"], "match_q32": 7},
                                {"insight_id": "i2", "sources": ["r8"], "match_q32": 3},
                            ]
                        },
                        "record_digest": "rec-1",
                    },
                }
            ]
        }
    )
    support.register_engine_decision_port(port)

    updated = await plan_runtime.apply_selected_insights(
        context, support.PORTS.engine_decision_port(), run_ref="r1"
    )

    assert port.calls == [
        ("insight_selection", params_digest({"context_signature": signature}))
    ]
    # The selected insights are appended in answer order; nothing else is added.
    assert len(updated) == 3
    assert updated[1]["insight_id"] == "i1"
    assert updated[2]["insight_id"] == "i2"


@pytest.mark.spec(SPEC_RECORD)
def test_injected_insights_recorded_as_one_run_event():
    state = GraphState(run_id="r1", query="q")
    selected = (
        {"insight_id": "i1", "sources": ["r9"], "match_q32": 7},
        {"insight_id": "i2", "sources": ["r8"], "match_q32": 3},
    )

    event = plan_runtime.record_injected_insights(selected, state)

    assert event["type"] == "insights_injected"
    assert event["run_id"] == "r1"
    assert event["sequence"] == 0
    assert state.event_sequence == 1
    # One entry per injected insight, in injection order.
    assert event["insights"] == [
        "insight:id=i1;sources=r9;match_q32=7",
        "insight:id=i2;sources=r8;match_q32=3",
    ]
