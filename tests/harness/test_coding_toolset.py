"""The coding toolset reaches the changeset cycle through the run loop."""

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

EDITS = [{"path": "a.txt", "search": "before", "replace": "after"}]


def _plan(tool: str, args: dict, seen: list):
    def respond(messages, info):
        last = messages[-1].parts[-1]
        if getattr(last, "part_kind", "") == "tool-return":
            seen.append(last.content)
            return ModelResponse(parts=[TextPart("done")])
        return ModelResponse(parts=[ToolCallPart(tool, json.dumps(args))])

    return RunPlan(
        model=FunctionModel(respond),
        steps=("code",),
        harness=HarnessConfig(coding=CodingConfig()),
    )


async def _run(plan):
    request = support.request(task="t")
    return [event async for event in iterate_run(request, plan=plan)]


@pytest.mark.spec("MERISTEM-SCM-R010")
async def test_execute_graph_runs_the_changeset_cycle_through_the_toolset():
    support.reset()
    source = Source([Submitted("cs-1")])
    support.PORTS.changeset_submitter = lambda run_id: source
    support.PORTS.gate_runner = lambda run_id: source
    seen: list = []
    events = await _run(
        _plan("submit_changeset", {"edits": EDITS, "fast_gates": ["true"]}, seen)
    )
    assert events[-1]["result"]["status"] == "completed"
    assert seen == ["cs-1"] and len(source.submitted) == 1
    assert source.submitted[0].edits[0].path == "a.txt"

    # An absent submitter, then an absent gate runner, each refuse by name.
    seen.clear()
    support.PORTS.changeset_submitter = None
    await _run(
        _plan("submit_changeset", {"edits": EDITS, "fast_gates": ["true"]}, seen)
    )
    assert seen == ["submitter_unavailable"]
    seen.clear()
    support.PORTS.changeset_submitter = lambda run_id: source
    support.PORTS.gate_runner = None
    await _run(
        _plan("submit_changeset", {"edits": EDITS, "fast_gates": ["true"]}, seen)
    )
    assert seen == ["sandbox_unavailable"]


@pytest.mark.spec("MERISTEM-SCM-R011.3")
async def test_execute_graph_reaches_run_task_run():
    from meristem.harness.task_run import TaskRunContext

    support.reset()
    source = Source([Submitted("cs-9")])
    support.PORTS.changeset_submitter = lambda run_id: source
    support.PORTS.gate_runner = lambda run_id: source
    context = TaskRunContext("t1", "ROW-1", "do it", base_digest="head-0")
    seen: list = []
    plan = _plan("run_task", {"edits": EDITS, "fast_gates": ["true"]}, seen)
    plan = RunPlan(
        model=plan.model,
        steps=plan.steps,
        harness=HarnessConfig(coding=CodingConfig(task_run=context)),
    )
    await _run(plan)
    assert seen[0]["status"] == "completed" and seen[0]["changeset_id"] == "cs-9"
    assert seen[0]["evidence"]["base_digest"] == "head-0"
    # A run without a context exposes no run_task tool: the model is told it is unknown.
    seen.clear()
    plain = _plan("run_task", {"edits": EDITS, "fast_gates": ["true"]}, seen)
    await _run(plain)
    assert "Unknown tool name: 'run_task'" in seen[0]
