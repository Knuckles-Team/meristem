"""Spans of a parent run and its children export with run correlation."""

from __future__ import annotations

import ports_support as support
import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from support.journey import bind_caller, execute
from support.recorded_engine import RecordedEnginePort

from meristem.graph import DependencyPlan, HarnessConfig, RunPlan, RunTask, ToolStep
from meristem.harness.span_export import build_spans
from meristem.host_ports import EngineAnswer


class SpanEngine(RecordedEnginePort):
    def __init__(self, refusal_code=None):
        super().__init__({"answers": []})
        self.nodes: list[dict] = []
        self.refusal_code = refusal_code

    async def write(self, method, params, *, run_ref):
        assert method == "AgentComponent" and params["op"] == "add_node"
        self.nodes.append(params["node"])
        if self.refusal_code:
            return EngineAnswer(outcome="abstained", refusal_code=self.refusal_code)
        return EngineAnswer(outcome="committed", record_digest=f"d{len(self.nodes)}")


def parent_with_two_children() -> RunPlan:
    async def child(messages, info):
        return ModelResponse(parts=[TextPart("child answer")])

    tasks = (
        RunTask("child-a", prompt="a"),
        RunTask("child-b", prompt="b"),
        RunTask(
            "fetch",
            depends_on=("child-a", "child-b"),
            call=ToolStep("srv", "fetch_doc", {}),
        ),
    )
    return RunPlan(
        model=FunctionModel(child),
        dependency_plan=DependencyPlan(
            tasks=tasks, output_task_id="fetch", plan_id="sha256:plan"
        ),
        harness=HarnessConfig(consumers=("span_export",)),
    )


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R070")
async def test_child_spans_correlate_to_parent(events):
    bind_caller()
    engine = SpanEngine()
    support.register_engine_decision_port(engine)
    result = await execute(parent_with_two_children())
    assert result["status"] == "completed"
    spans = [node["properties"] for node in engine.nodes]
    assert {node["label"] for node in engine.nodes} == {"RunTrace"}
    assert [s["child_agent"] or s["tool_call_id"] for s in spans] == [
        "child-a",
        "child-b",
        "fetch",
    ]
    assert {s["parent_run_id"] for s in spans} == {"r1"}
    assert [s["kind"] for s in spans] == ["agent", "agent", "tool"]
    assert spans[2]["tool_call_id"] == "fetch" and spans[2]["name"] == "fetch_doc"
    (exported,) = [e for e in events if e.get("type") == "spans_exported"]
    assert exported["count"] == 3


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R070")
async def test_an_engine_refusal_or_unbound_port_fails_the_export():
    bind_caller()
    support.register_engine_decision_port(SpanEngine(refusal_code="RUN_TRACE_INVALID"))
    refused = await execute(parent_with_two_children())
    assert "RUN_TRACE_INVALID" in refused["error"]
    support.register_engine_decision_port(None)
    unbound = await execute(parent_with_two_children())
    assert "engine_decision_unavailable" in unbound["error"]


def test_spans_of_another_run_are_not_exported():
    events = [
        {
            "type": "task_completed",
            "run_id": "other",
            "task_id": "t",
            "event_id": "e",
            "sequence": 1,
        },
    ]
    assert build_spans("r1", events) == []
