"""Against a host that does not serve the run start yet, the harness refuses by name.

The served proofs of MERISTEM-SCM-R011.5 and MERISTEM-SCM-R012.8 need the arbora run
start and assignment start; until arbora serves them, those rows stay unbound and
these tests pin the refusal states instead.
"""

from __future__ import annotations

import json

import ports_support as support
import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from support.coding_fakes import Gates, Heads

from meristem.graph import RunPlan
from meristem.graph.executor import iterate_run
from meristem.graph.state import CodingConfig, HarnessConfig
from meristem.harness.delivery_run import (
    DeliveryAssignmentContext,
    DeliveryRun,
    revise_proposal,
)
from meristem.harness.edit_engine import Edit
from meristem.harness.task_run import TaskRunContext
from meristem.host_ports import EngineDecisionRefused, HostPorts
from meristem.runtime.local_workspace import LocalWorkspace
from meristem.runtime.workspace import DevWorkspace

EDITS = [{"path": "a.txt", "search": "before", "replace": "after"}]


async def test_task_run_without_bound_submitter_returns_the_typed_failure():
    seen: list = []

    def respond(messages, info):
        last = messages[-1].parts[-1]
        if getattr(last, "part_kind", "") == "tool-return":
            seen.append(last.content)
            return ModelResponse(parts=[TextPart("done")])
        args = {"edits": EDITS, "fast_gates": ["true"]}
        return ModelResponse(parts=[ToolCallPart("run_task", json.dumps(args))])

    context = TaskRunContext("t1", "ROW-1", "do it", base_digest="head-0")
    plan = RunPlan(
        model=FunctionModel(respond),
        steps=("code",),
        harness=HarnessConfig(coding=CodingConfig(task_run=context)),
    )
    support.reset()
    events = [e async for e in iterate_run(support.request(task="t"), plan=plan)]
    assert events[-1]["result"]["status"] == "completed"
    failure = json.loads(seen[0]) if isinstance(seen[0], str) else seen[0]
    assert failure["status"] == "failed"
    assert failure["failure"]["code"] == "submitter_unavailable"


async def test_delivery_revision_without_an_engine_port_pushes_nothing():
    run = DeliveryRun(DeliveryAssignmentContext("d1", proposal_ref="pr-1"), HostPorts())
    source = Heads()
    workspace = DevWorkspace(LocalWorkspace(), run_id="r")
    try:
        with pytest.raises(EngineDecisionRefused) as raised:
            await revise_proposal(
                run,
                workspace,
                (Edit("a.txt", "before", "after"),),
                base_head="head-0",
                fast_gates=("t",),
                submitter=source,
                gate_runner=Gates({"t": (0, "")}),
                run_ref="d1",
            )
    finally:
        await workspace.stop()
    assert raised.value.code == "engine_decision_unavailable" and source.heads == []
