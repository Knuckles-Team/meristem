"""One edit round with a declared prediction and a received verdict (MERISTEM-SCM-R005.1)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from meristem.harness.changeset_loop import ChangesetSubmitter, SandboxGateRunner
from meristem.harness.edit_engine import Edit
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineDecisionPort
from meristem.runtime.workspace import DevWorkspace

_PREDICTION_KEYS = frozenset({"round", "fixes", "regressions"})


def parse_prediction(raw: object) -> dict[str, Any] | None:
    """The closed ``PredictionDeclaration`` schema, or ``None`` when malformed."""
    if not isinstance(raw, Mapping) or set(raw) != _PREDICTION_KEYS:
        return None
    rnd, fixes, regressions = raw["round"], raw["fixes"], raw["regressions"]
    if type(rnd) is not int or rnd < 0:
        return None
    for ids in (fixes, regressions):
        if not isinstance(ids, (list, tuple)) or not all(
            type(i) is str and i for i in ids
        ):
            return None
    return {"round": rnd, "fixes": list(fixes), "regressions": list(regressions)}


async def _passes(
    tasks: Mapping[str, str], runner: SandboxGateRunner, workspace: DevWorkspace
) -> dict[str, bool]:
    return {
        task_id: (await runner.run_gate(workspace, command)).exit_code == 0
        for task_id, command in tasks.items()
    }


async def run_edit_round(
    edits: tuple[Edit, ...],
    prediction: object,
    tasks: Mapping[str, str],
    runner: SandboxGateRunner,
    submitter: ChangesetSubmitter,
    workspace: DevWorkspace,
    port: EngineDecisionPort | None,
    *,
    base_head: str,
    run_ref: str,
    emit: Callable[[Mapping[str, Any]], None],
) -> str:
    """Return ``kept``, ``reverted`` or the step observation ``prediction_malformed``.

    Nothing is applied for a malformed prediction. The edit stays only on a
    ``verified`` verdict; any other answer, including an abstention or
    ``ROUND_NO_PREDICTION``, restores the base head.
    """
    parsed = parse_prediction(prediction)
    if parsed is None:
        return "prediction_malformed"
    if port is None:
        raise ConsumerRefused("engine_decision_unavailable")
    if not tasks:
        raise ConsumerRefused("round_tasks_missing")
    await workspace.start()
    await submitter.materialize(base_head, workspace)
    before = await _passes(tasks, runner, workspace)
    if await runner.apply_edits(workspace, edits) is not True:
        await submitter.materialize(base_head, workspace)
        return "reverted"
    after = await _passes(tasks, runner, workspace)
    outcomes = [
        {"task_id": t, "baseline_pass": before[t], "after_pass": after[t]}
        for t in tasks
    ]
    answer = await port.decide(
        "RoundVerdict", {"prediction": parsed, "outcomes": outcomes}, run_ref=run_ref
    )
    verdict = (
        "verified"
        if answer.refusal_code is None and "verified" in answer.option_ids
        else "reverted"
    )
    emit(
        {
            "kind": "edit_round_verdict",
            "run_ref": run_ref,
            "verdict": verdict,
            "record_digest": answer.record_digest,
        }
    )
    if verdict == "verified":
        return "kept"
    await submitter.materialize(base_head, workspace)
    return "reverted"
