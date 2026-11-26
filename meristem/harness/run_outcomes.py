"""Run outcomes the harness reports to the engine, never judges.

Every function here sends what the run observed through the engine decision
port's decision-log write and keeps the engine's refusal out of the run result.
The engine measures, stores and decides; the harness only reports.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from meristem.harness import engine_step
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineDecisionPort, ToolStepResult


def run_success(status: Any) -> bool | None:
    """True for completed, False for failed, ``None`` (censored) otherwise."""
    return {"completed": True, "failed": False}.get(status.value)


async def _evaluate(
    port: EngineDecisionPort | None, body: Mapping[str, Any], run_ref: str
) -> str | None:
    """One ``Evaluate`` write; an engine refusal is returned, never raised."""
    try:
        await engine_step.record(port, "Evaluate", body, run_ref=run_ref)
    except ConsumerRefused as refused:
        return refused.code
    return None


async def report_outcomes(
    state: Any, port: EngineDecisionPort | None
) -> list[dict[str, Any]]:
    """Evaluate every applied decision of the run once (MERISTEM-HARNESS-CYCLE-R004.1).

    ``evaluation_id`` is ``<run_id>:<kind>`` so a repeat is idempotent. A
    refused evaluation never changes the run result; its code is kept in the
    returned trace entry.
    """
    success = run_success(state.status)
    trace: list[dict[str, Any]] = []
    for kind, digest in state.engine_records.items():
        evaluation_id = f"{state.run_id}:{kind}"
        refusal = await _evaluate(
            port,
            {"record_id": digest, "evaluation_id": evaluation_id, "success": success},
            state.run_id,
        )
        trace.append({"evaluation_id": evaluation_id, "refusal_code": refusal})
    return trace


async def evaluate_tool_outcome(
    result: ToolStepResult,
    port: EngineDecisionPort | None,
    *,
    run_id: str,
    task_id: str,
) -> str | None:
    """Evaluate one tool call against its routing record (MERISTEM-HARNESS-CYCLE-R025.1).

    A result without a routing record is skipped. Returns the evaluation id.
    """
    if result.routing_record_digest is None:
        return None
    success = {"ok": True, "failed": False}.get(result.outcome)
    evaluation_id = f"{run_id}:{task_id}"
    await _evaluate(
        port,
        {
            "record_id": result.routing_record_digest,
            "evaluation_id": evaluation_id,
            "success": success,
        },
        run_id,
    )
    return evaluation_id


async def record_experience(
    state: Any,
    experiences: Sequence[Mapping[str, Any]],
    port: EngineDecisionPort | None,
) -> list[dict[str, Any]]:
    """Record each action and task as one typed outcome (MERISTEM-HARNESS-CYCLE-R041.2)."""
    outcomes: list[dict[str, Any]] = []
    for item in experiences:
        subject = item.get("subject_ref")
        if type(subject) is not str or not subject:
            raise ConsumerRefused("experience_unkeyed")
        outcomes.append(
            {
                "subject_ref": subject,
                "kind": "task" if item.get("kind") == "task" else "action",
                "success": item.get("success"),
                "cost": item.get("cost"),
                "evidence_ref": item.get("evidence_ref"),
            }
        )
    for outcome in outcomes:
        await engine_step.record(port, "evaluate", outcome, run_ref=state.run_id)
    return outcomes


async def record_skill_outcome(
    state: Any,
    port: EngineDecisionPort | None,
    *,
    skill_ref: str,
    cost: int | float,
    package_digest: str | None,
) -> dict[str, Any]:
    """Learn one skill run with the digest of the package rendered for it (R051.1)."""
    if type(package_digest) is not str or not package_digest:
        raise ConsumerRefused("package_digest_missing")
    body = {
        "skill_ref": skill_ref,
        "success": state.status.value == "completed",
        "cost": cost,
        "package_digest": package_digest,
    }
    await engine_step.record(port, "Learn", body, run_ref=state.run_id)
    return body


async def emit_local_model_receipt(
    state: Any,
    port: EngineDecisionPort | None,
    *,
    model_ref: str,
    local: bool,
    attestation_ref: str | None,
    task_digest: str,
) -> dict[str, Any] | None:
    """Send one evaluation receipt per local-model run (MERISTEM-HARNESS-CYCLE-R049.1).

    A hosted model sends nothing; a local one without an attestation refuses.
    """
    if not local:
        return None
    if type(attestation_ref) is not str or not attestation_ref:
        raise ConsumerRefused("attestation_missing")
    body = {
        "model_ref": model_ref,
        "task_digest": task_digest,
        "outcome": state.status.value,
        "attestation_ref": attestation_ref,
    }
    await engine_step.write(port, "DecisionEval", body, run_ref=state.run_id)
    return body


__all__ = [
    "emit_local_model_receipt",
    "evaluate_tool_outcome",
    "record_experience",
    "record_skill_outcome",
    "report_outcomes",
    "run_success",
]
