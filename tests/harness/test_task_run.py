"""One task run: one cycle, typed failures, evidence (MERISTEM-SCM-R011.1 to R011.4)."""

from __future__ import annotations

import dataclasses

import pytest
from support.coding_fakes import Gates, Heads

from meristem.harness.changeset_loop import SourceConflict, Submitted
from meristem.harness.edit_engine import Edit
from meristem.harness.task_run import (
    TaskFailure,
    TaskRunContext,
    TaskRunResult,
    run_task_run,
)
from meristem.host_ports import HostPorts
from meristem.runtime.local_workspace import LocalWorkspace
from meristem.runtime.workspace import DevWorkspace

EDITS = (Edit("a.txt", "before", "after"),)
CONTEXT = TaskRunContext("t1", "ROW-1", "do the thing", base_digest="head-0")


class Source(Heads):
    def __init__(self, *outcomes):
        super().__init__()
        self.outcomes = list(outcomes)
        self.submits = 0

    async def materialize(self, head, workspace):
        await super().materialize(head, workspace)
        (workspace.backend.root / "a.txt").write_text("before\n")

    async def submit(self, pending):
        self.submits += 1
        return self.outcomes.pop(0)


def _ws():
    return DevWorkspace(LocalWorkspace(), run_id="r")


async def _run(context, source, gates, gate_cmds=("t",)):
    workspace = _ws()
    try:
        return await run_task_run(
            context,
            EDITS,
            gate_cmds,
            submitter=source,
            gate_runner=gates,
            workspace=workspace,
            ports=HostPorts(),
        )
    finally:
        await workspace.stop()


@pytest.mark.spec("MERISTEM-SCM-R011.1")
def test_context_and_result_are_frozen_and_round_trip():
    result = TaskRunResult("completed", "cs")
    assert dataclasses.asdict(CONTEXT)["row_id"] == "ROW-1"
    with pytest.raises(dataclasses.FrozenInstanceError):
        CONTEXT.row_id = "x"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.status = "failed"  # type: ignore[misc]
    assert TaskFailure("c", "s").evidence.changeset_id == ""


@pytest.mark.spec("MERISTEM-SCM-R011.2")
async def test_one_cycle_per_task_and_incomplete_context_refuses():
    source = Source(Submitted("cs-1"))
    gates = Gates({"t": (0, "")}, files={"a.txt": "after\n"})
    result = await _run(CONTEXT, source, gates)
    assert result.status == "completed" and source.submits == 1
    for bad in (
        dataclasses.replace(CONTEXT, row_text=" "),
        dataclasses.replace(CONTEXT, base_digest=""),
    ):
        source = Source()
        refused = await _run(bad, source, Gates({}))
        assert refused.failure and refused.failure.code == "task_context_incomplete"
        assert source.heads == [] and source.submits == 0


@pytest.mark.spec("MERISTEM-SCM-R011.4")
async def test_each_failure_returns_code_stage_and_evidence():
    files = {"a.txt": "after\n"}
    ok = await _run(CONTEXT, Source(Submitted("cs-1")), Gates({"t": (0, "")}, files))
    assert ok.evidence.changeset_id == "cs-1" and ok.evidence.gate_results == ((0, 0),)
    assert ok.evidence.test_results == (("t", 0),)
    assert ok.evidence.base_digest == "head-0"

    gate = await _run(CONTEXT, Source(), Gates({"t": (2, "")}, files))
    assert (gate.failure.code, gate.failure.stage) == ("gate_failed", "gate")
    assert gate.failure.evidence.gate_results == ((0, 2),)

    conflicts = [SourceConflict(("a.txt",))] * 4
    spent = await _run(CONTEXT, Source(*conflicts), Gates({"t": (0, "")}, files))
    assert (spent.failure.code, spent.failure.stage) == (
        "conflict_retry_exhausted",
        "submit",
    )

    none = await _run(CONTEXT, None, Gates({}))
    assert none.failure.code == "submitter_unavailable"
    nogate = await _run(CONTEXT, Source(), None)
    assert (nogate.failure.code, nogate.failure.stage) == (
        "sandbox_unavailable",
        "gate",
    )
