"""One received task, one changeset cycle, a result with evidence (MERISTEM-SCM-R011).

The harness holds no planner: it never picks the next task, splits a task or
edits a spec. Every field of the context is a received value.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any

from meristem.harness.changeset_loop import (
    ChangesetSubmitter,
    SandboxGateRunner,
    run_changeset_cycle,
)
from meristem.harness.edit_engine import Edit
from meristem.host_ports import HostPorts
from meristem.runtime.workspace import DevWorkspace


@dataclass(frozen=True)
class TaskRunContext:
    task_id: str
    row_id: str
    row_text: str
    plan_sections: tuple[str, ...] = ()
    contracts: tuple[str, ...] = ()
    data_model: tuple[str, ...] = ()
    code_neighbourhood: tuple[str, ...] = ()
    base_digest: str = ""


@dataclass(frozen=True)
class TaskEvidence:
    """Gate results, changeset id, test results and the base digest of one run."""

    gate_results: tuple[tuple[int, int], ...] = ()
    changeset_id: str = ""
    test_results: tuple[tuple[str, int], ...] = ()
    base_digest: str = ""


@dataclass(frozen=True)
class TaskFailure:
    code: str
    stage: str
    evidence: TaskEvidence = field(default_factory=TaskEvidence)


@dataclass(frozen=True)
class TaskRunResult:
    status: str
    changeset_id: str = ""
    evidence: TaskEvidence = field(default_factory=TaskEvidence)
    failure: TaskFailure | None = None


#: The cycle reasons that name a stage; any other reason is an edit-stage failure.
_STAGES: Mapping[str, str] = MappingProxyType(
    {
        "gate_failed": "gate",
        "gate_modified_edits": "gate",
        "sandbox_unavailable": "gate",
        "conflict_retry_exhausted": "submit",
        "submitter_unavailable": "submit",
        "submit_refused": "submit",
    }
)


async def run_task_run(
    context: TaskRunContext,
    edits: tuple[Edit, ...],
    fast_gates: tuple[str, ...],
    *,
    submitter: ChangesetSubmitter | None,
    gate_runner: SandboxGateRunner | None,
    workspace: DevWorkspace,
    ports: HostPorts,
) -> TaskRunResult:
    """Run one cycle for the received task and return the result with evidence.

    An empty ``row_text`` or ``base_digest`` refuses ``task_context_incomplete``
    with zero edits and zero submits. A failed cycle is never retried here;
    the rebase bound of the cycle is the only retry.
    """
    if not context.row_text.strip() or not context.base_digest.strip():
        failure = TaskFailure("task_context_incomplete", "context")
        return TaskRunResult("failed", failure=failure)
    gates: list[tuple[int, int]] = []

    def collect(event: Mapping[str, Any]) -> None:
        if event.get("kind") == "changeset_gate":
            gates.append((int(event["gate_index"]), int(event["exit_code"])))
        ports.emit_run_event(event)

    result = await run_changeset_cycle(
        workspace,
        edits,
        base_head=context.base_digest,
        run_ref=context.task_id,
        fast_gates=fast_gates,
        submitter=submitter,
        gate_runner=gate_runner,
        ports=replace(ports, events=collect),
    )
    evidence = TaskEvidence(
        gate_results=tuple(gates),
        changeset_id=result.changeset_id,
        test_results=tuple((fast_gates[i], code) for i, code in gates),
        base_digest=context.base_digest,
    )
    if result.reason == "submitted":
        return TaskRunResult("completed", result.changeset_id, evidence)
    stage = _STAGES.get(result.reason, "edit")
    return TaskRunResult(
        "failed", failure=TaskFailure(result.reason, stage, evidence), evidence=evidence
    )
