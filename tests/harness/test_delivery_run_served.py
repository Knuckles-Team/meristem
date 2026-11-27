"""Served delivery run: one proposal iterates through CI and review (MERISTEM-SCM-R012.8)."""

from __future__ import annotations

import pytest
from support.coding_fakes import Gates, Heads
from support.scripted_port import ScriptedPort, answer

from meristem.harness.changeset_loop import Submitted
from meristem.harness.delivery_run import (
    DeliveryAssignmentContext,
    DeliveryRun,
    deliver_inputs,
    dispose_thread,
    report,
    revise_proposal,
)
from meristem.harness.edit_engine import Edit
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import (
    ActiveChildBinding,
    ActiveChildMessage,
    HostPorts,
    ToolStepResult,
)
from meristem.runtime.local_workspace import LocalWorkspace
from meristem.runtime.workspace import DevWorkspace

CONTEXT = DeliveryAssignmentContext("d1", ("ROW-1",), "pr-7")
BINDING = ActiveChildBinding("c", "p", "a", 1)


class _Source(Heads):
    def __init__(self):
        super().__init__()
        self.submits = 0

    async def materialize(self, head, workspace):
        await super().materialize(head, workspace)
        (workspace.backend.root / "a.txt").write_text("before\n")

    async def submit(self, pending):
        self.submits += 1
        return Submitted(f"cs-{self.submits}")


class _Inputs:
    binding = BINDING

    def __init__(self, batches):
        self.batches, self.applied = list(batches), []

    async def take_ready(self):
        return tuple(self.batches.pop(0)) if self.batches else ()

    async def report_applied(self, message):
        self.applied.append(message.message_id)


def _host(port, calls, events) -> HostPorts:
    async def caller(server, tool, arguments, task_id):
        calls.append((tool, arguments))
        return ToolStepResult(result={})

    return HostPorts(events=events.append, caller=caller, engine_decision=port)


async def _revise(run: DeliveryRun, source: _Source) -> str:
    workspace = DevWorkspace(LocalWorkspace(), run_id="r")
    try:
        result = await revise_proposal(
            run,
            workspace,
            (Edit("a.txt", "before", "after"),),
            base_head="head-0",
            fast_gates=("t",),
            submitter=source,
            gate_runner=Gates({"t": (0, "")}, files={"a.txt": "after\n"}),
            run_ref="d1",
        )
        return result.reason
    finally:
        await workspace.stop()


@pytest.mark.spec("MERISTEM-SCM-R012.8")
async def test_served_delivery_run_iterates_one_proposal():
    calls: list = []
    events: list = []
    port = ScriptedPort(
        merge_arming=answer({"verdict": "disarm"}),
    )
    run = DeliveryRun(CONTEXT, _host(port, calls, events))
    source = _Source()
    inputs = _Inputs(
        [
            [ActiveChildMessage(BINDING, "m1", "e1", "ci failed")],
            [ActiveChildMessage(BINDING, "m2", "e2", "review finding")],
        ]
    )
    revised: list[str] = []

    async def apply(message):
        revised.append(await _revise(run, source))
        if message.message_id == "m2":
            assert await dispose_thread(run, {"thread_id": "t1"}, "fixed") is None

    assert await deliver_inputs(run, inputs, apply) == 1
    assert await deliver_inputs(run, inputs, apply) == 1
    assert inputs.applied == ["m1", "m2"]
    assert revised == ["submitted", "submitted"] and source.submits == 2
    assert run.context.proposal_ref == "pr-7"  # one proposal, never a second
    assert [c[0] for c in calls].count("thread.resolve") == 1
    done = report(run, "ready", "all green", reviewed=True, gates_passed=True)
    assert done.state == "ready" and done.proposal_ref == "pr-7"
    assert events[-1]["kind"] == "delivery_report" and events[-1]["state"] == "ready"

    # No arming verdict: the run refuses and pushes nothing.
    silent = DeliveryRun(CONTEXT, _host(ScriptedPort(merge_arming=answer({})), [], []))
    none = _Source()
    with pytest.raises(ConsumerRefused) as refused:
        await _revise(silent, none)
    assert refused.value.code == "delivery_arming_verdict_missing"
    assert none.submits == 0 and none.heads == []
