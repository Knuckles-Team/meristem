"""Bind a generated fix to a regression test run on both revisions (MERISTEM-SCM-R007.1)."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from typing import Any

from meristem.harness.changeset_loop import ChangesetSubmitter, SandboxGateRunner
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineDecisionPort
from meristem.runtime.workspace import DevWorkspace


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def bind_correction(
    *,
    run_id: str,
    trace_id: str,
    failure_point: str,
    fix_id: str,
    changeset_digest: str,
    test_id: str,
    command: str | None,
    before_head: str,
    after_head: str,
    runner: SandboxGateRunner,
    submitter: ChangesetSubmitter,
    workspace: DevWorkspace,
    port: EngineDecisionPort | None,
    emit: Callable[[Mapping[str, Any]], None],
) -> bool:
    """Run the regression test on both revisions and ask for admission.

    The test must fail before and pass after; the harness reports the
    observed outcomes and never edits them. Returns ``True`` only on an
    ``admit`` answer, so the caller submits only then.
    """
    if port is None:
        raise ConsumerRefused("engine_decision_unavailable")
    if not command:
        raise ConsumerRefused("regression_command_missing")
    await workspace.start()
    receipts: list[dict[str, str]] = []
    for head in (before_head, after_head):
        await submitter.materialize(head, workspace)
        observation = await runner.run_gate(workspace, command)
        receipts.append(
            {
                "outcome": "passed" if observation.exit_code == 0 else "failed",
                "revision_id": head,
                "test_id": test_id,
            }
        )
    binding = {
        "trace": {
            "run_id": run_id,
            "trace_id": trace_id,
            "failure_point": failure_point,
            "error_trace_digest": _sha(failure_point),
        },
        "fix": {
            "fix_id": fix_id,
            "trace_id": trace_id,
            "fix_digest": changeset_digest,
        },
        "test": {
            "test_id": test_id,
            "fix_id": fix_id,
            "before_revision": before_head,
            "after_revision": after_head,
            "test_digest": _sha(command),
        },
        "receipts": receipts,
    }
    answer = await port.decide("CorrectionAdmission", binding, run_ref=run_id)
    if answer.refusal_code is not None:
        raise ConsumerRefused(answer.refusal_code)
    emit({"kind": "correction_bound", "run_ref": run_id, "binding": binding})
    return "admit" in answer.option_ids
