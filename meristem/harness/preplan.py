"""Pre-plan test receipt and the compiled-program commit (MERISTEM-SCM-R004.1)."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from meristem.harness.changeset_loop import SandboxGateRunner
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineDecisionPort
from meristem.runtime.workspace import DevWorkspace


@dataclass(frozen=True)
class PrePlanTestReceipt:
    artifact_count: int
    artifact_manifest_digest: str
    command_digest: str
    command_id: str
    completed_at_ms: int
    exit_code: int
    repository_id: str
    repository_revision: str
    result_digest: str
    run_id: str
    started_at_ms: int
    tenant_id: str


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def run_preplan(
    command: str | None,
    artifacts: Sequence[str],
    runner: SandboxGateRunner,
    workspace: DevWorkspace,
    port: EngineDecisionPort | None,
    *,
    run_id: str,
    repository_id: str,
    revision: str,
    tenant_id: str = "",
    clock_ms: Callable[[], int] = lambda: int(time.time() * 1000),
) -> PrePlanTestReceipt | None:
    """Run the declared command before planning; ``None`` when the step is off.

    A missing command or artifact list skips the step. The manifest of path
    and file-digest pairs is digested into the receipt, which the later
    program commit carries as its body.
    """
    if not command or not artifacts:
        return None
    if port is None:
        raise ConsumerRefused("engine_decision_unavailable")
    started = clock_ms()
    observation = await runner.run_gate(workspace, command)
    completed = clock_ms()
    paths = tuple(sorted(artifacts))
    pairs: list[list[str]] = []
    if observation.exit_code == 0:
        files = await runner.read_files(workspace, paths)
        pairs = [[p, _sha(c.encode("utf-8"))] for p, c in files]
    manifest = json.dumps(pairs, separators=(",", ":")).encode("utf-8")
    return PrePlanTestReceipt(
        artifact_count=len(pairs),
        artifact_manifest_digest=_sha(manifest),
        command_digest=_sha(command.encode("utf-8")),
        command_id=_sha(command.encode("utf-8"))[:16],
        completed_at_ms=completed,
        exit_code=observation.exit_code,
        repository_id=repository_id,
        repository_revision=revision,
        result_digest=_sha(
            json.dumps([observation.exit_code, observation.stdout]).encode("utf-8")
        ),
        run_id=run_id,
        started_at_ms=started,
        tenant_id=tenant_id,
    )


async def commit_compiled_program(
    receipt: PrePlanTestReceipt | None,
    port: EngineDecisionPort | None,
    *,
    run_ref: str,
    emit: Callable[[Mapping[str, Any]], None],
) -> dict[str, Any]:
    """Attach the receipt to the program commit; ``reusable`` follows the receipt.

    ``reusable`` is true only for exit code 0 with a positive artifact count;
    without a receipt it is false and nothing is committed.
    """
    if receipt is None:
        return {"reusable": False, "receipt": None}
    if port is None:
        raise ConsumerRefused("engine_decision_unavailable")
    answer = await port.log("RunRecord", asdict(receipt), run_ref=run_ref)
    if answer.refusal_code is not None:
        raise ConsumerRefused(answer.refusal_code)
    emit(
        {
            "kind": "preplan_receipt_committed",
            "run_ref": run_ref,
            "result_digest": receipt.result_digest,
        }
    )
    return {
        "reusable": receipt.exit_code == 0 and receipt.artifact_count > 0,
        "receipt": asdict(receipt),
        "record_digest": answer.record_digest,
    }
