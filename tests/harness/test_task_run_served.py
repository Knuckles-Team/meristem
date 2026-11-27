"""Served task run: the run loop returns the result and evidence (MERISTEM-SCM-R011.5)."""

from __future__ import annotations

import json

import ports_support as support
import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from test_changeset_loop import Source

from meristem.graph import RunPlan
from meristem.graph.executor import iterate_run
from meristem.graph.state import CodingConfig, HarnessConfig
from meristem.harness.changeset_loop import Submitted
from meristem.harness.task_run import TaskRunContext

EDITS = [{"path": "a.txt", "search": "before", "replace": "after"}]
CONTEXT = TaskRunContext("t1", "ROW-1", "do the thing", base_digest="head-0")


async def _serve(gates: list[str], source: Source) -> tuple[list, dict]:
    """Start one run as the host does and return its events and the tool result."""
    support.reset()
    support.PORTS.changeset_submitter = lambda run_id: source
    support.PORTS.gate_runner = lambda run_id: source
    observed: list = []

    def respond(messages, info):
        last = messages[-1].parts[-1]
        if getattr(last, "part_kind", "") == "tool-return":
            observed.append(last.content)
            return ModelResponse(parts=[TextPart("done")])
        args = {"edits": EDITS, "fast_gates": gates}
        return ModelResponse(parts=[ToolCallPart("run_task", json.dumps(args))])

    plan = RunPlan(
        model=FunctionModel(respond),
        steps=("code",),
        harness=HarnessConfig(coding=CodingConfig(task_run=CONTEXT)),
    )
    events = [e async for e in iterate_run(support.request(task="t"), plan=plan)]
    return events, observed[0]


@pytest.mark.spec("MERISTEM-SCM-R011.5")
async def test_served_task_run_returns_result_and_evidence():
    source = Source([Submitted("cs-1")])
    events, result = await _serve(["true"], source)
    assert events[-1]["result"]["status"] == "completed"
    assert result["status"] == "completed" and result["changeset_id"] == "cs-1"
    assert result["evidence"]["changeset_id"] == "cs-1"
    assert result["evidence"]["base_digest"] == "head-0"
    assert len(source.submitted) == 1

    failing = Source([Submitted("cs-2")])
    _, failed = await _serve(["false"], failing)
    assert failed["failure"]["code"] == "gate_failed"
    assert failed["failure"]["stage"] == "gate"
    assert failing.submitted == []
