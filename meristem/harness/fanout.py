"""Fan-out consumers: child summaries, trajectory rungs, analyst findings, aggregation.

Each step applies what the engine returned and decides nothing: the harness never
picks a rung, a width, a winner or a coordinator (MERISTEM-HARNESS-CYCLE-R021).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from meristem.harness import engine_step
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineDecisionPort


@dataclass(frozen=True, slots=True)
class ChildSummary:
    """The one artifact a child returns; the parent keeps the digest only."""

    artifact_kind: str
    schema_id: str
    schema_version: str
    summary_digest: str
    summary_tokens: int
    tokenizer_revision: str


def accept_child_summary(
    summary: ChildSummary, *, max_child_summary_tokens: int
) -> dict[str, Any]:
    """Accept a child's summary (R021.2) and return the trace entry.

    A child artifact of any other kind refuses ``child_returned_raw_output``; a
    summary over the received token limit refuses ``child_summary_over_limit``.
    """
    if summary.artifact_kind != "summary":
        raise ConsumerRefused("child_returned_raw_output")
    if summary.summary_tokens > max_child_summary_tokens:
        raise ConsumerRefused("child_summary_over_limit")
    return {
        "summary_digest": summary.summary_digest,
        "summary_tokens": summary.summary_tokens,
    }


@dataclass(frozen=True, slots=True)
class ArmResult:
    """One trajectory arm: the snapshot it read and what it produced."""

    arm: int
    snapshot_digest: str
    result: Any


async def run_trajectory_arms(
    port: EngineDecisionPort | None,
    *,
    run_id: str,
    task_digest: str,
    input_snapshot_digest: str,
    max_width: int,
    rungs: Sequence[str],
    run_arm: Callable[[int, int], Awaitable[ArmResult]],
) -> dict[str, Any]:
    """Run the selected rung (R021.4): ``width`` arms of ``depth`` plus the baseline.

    ``run_arm(arm, depth)`` runs one arm. Every arm must read the input snapshot,
    else the step refuses ``snapshot_mismatch``. The baseline is arm 0, width 1
    and depth 1.
    """
    answer = await engine_step.decide(
        port,
        "trajectory_ladder",
        {
            "task_digest": task_digest,
            "input_snapshot_digest": input_snapshot_digest,
            "max_width": max_width,
            "rungs": list(rungs),
        },
        run_ref=run_id,
    )
    width = int(answer.payload["width"])
    depth = int(answer.payload["depth"])
    arms = [await run_arm(0, 1)]
    arms.extend([await run_arm(index, depth) for index in range(1, width + 1)])
    if any(arm.snapshot_digest != input_snapshot_digest for arm in arms):
        raise ConsumerRefused("snapshot_mismatch")
    return {
        "rung_id": answer.payload.get("rung_id"),
        "snapshot_digest": input_snapshot_digest,
        "arms": [{"arm": arm.arm, "result": arm.result} for arm in arms],
    }


async def collect_analyst_findings(
    port: EngineDecisionPort | None,
    *,
    run_id: str,
    roles: Sequence[str],
    run_role: Callable[[str], Awaitable[Sequence[Mapping[str, Any]]]],
) -> dict[str, Any]:
    """Collect one finding or named gap per role, then apply the cited synthesis (R021.5)."""
    collected: list[Mapping[str, Any]] = []
    for role in roles:
        found = list(await run_role(role))
        if len(found) > 1:
            raise ConsumerRefused("analyst_role_duplicate")
        collected.append(
            {"role": role, **(found[0] if found else {"gap": "no_finding"})}
        )
    answer = await engine_step.decide(
        port, "analyst_synthesis", {"findings": collected}, run_ref=run_id
    )
    return {"findings": collected, "synthesis": dict(answer.payload)}


@dataclass(frozen=True, slots=True)
class FanoutSubtaskResult:
    """One planned subtask's result, with its receipt or its named gap."""

    task_id: str
    status: str
    result_digest: str | None = None
    receipt_id: str | None = None
    receipt_digest: str | None = None
    failure_gap: str | None = None


async def submit_fanout_results(
    port: EngineDecisionPort | None,
    *,
    run_id: str,
    results: Sequence[FanoutSubtaskResult],
) -> str | None:
    """Submit the fan-out result for aggregation (R021.7); returns the aggregate digest."""
    for item in results:
        if item.status == "ok" and not (item.receipt_id and item.receipt_digest):
            raise ConsumerRefused("receipt_missing")
    answer = await engine_step.decide(
        port,
        "fanout_aggregate",
        {
            "results": [
                {
                    "task_id": r.task_id,
                    "status": r.status,
                    "result_digest": r.result_digest,
                    "receipt_id": r.receipt_id,
                    "receipt_digest": r.receipt_digest,
                    "failure_gap": r.failure_gap,
                }
                for r in results
            ]
        },
        run_ref=run_id,
    )
    return answer.record_digest


def run_coordinator(
    output_task_id: str,
    artifacts: Sequence[Mapping[str, Any]],
    declaration: Mapping[str, Any],
) -> dict[str, Any]:
    """Deliver exactly one final artifact of the declared shape (R021.8).

    The plan's output task is the coordinator. Zero or two artifacts, or one
    that does not match ``FinalArtifactDecl``, refuse ``final_artifact_count``.
    """
    shape = ("schema_id", "schema_version", "kind")
    if len(artifacts) != 1 or any(
        artifacts[0].get(key) != declaration.get(key) for key in shape
    ):
        raise ConsumerRefused("final_artifact_count")
    return {"coordinator": output_task_id, "digest": artifacts[0].get("digest")}


__all__ = [
    "ArmResult",
    "ChildSummary",
    "FanoutSubtaskResult",
    "accept_child_summary",
    "collect_analyst_findings",
    "run_coordinator",
    "run_trajectory_arms",
    "submit_fanout_results",
]
