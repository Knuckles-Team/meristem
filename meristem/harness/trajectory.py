"""Trajectory consumers: budget, projection, breadth, comparisons (MERISTEM-HARNESS-CYCLE-R039, R048).

Every number here is a received value: the harness starts exactly the breadth it
was told, never exceeds the returned count, and reorders nothing else.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from meristem.harness import engine_step
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineDecisionPort, EngineDecisionRefused


@dataclass(frozen=True, slots=True)
class TrajectoryBudget:
    """The received budget decision every later trajectory request carries."""

    decision_id: str
    count: int

    def tag(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Attach the decision id to one trajectory request event (R048.1)."""
        if not self.decision_id:
            raise ConsumerRefused("budget_decision_unbound")
        return {**request, "decision_id": self.decision_id}


async def bind_budget(
    port: EngineDecisionPort | None, params: Mapping[str, Any], *, run_id: str
) -> TrajectoryBudget:
    """Ask ``TaskPlan`` once for the trajectory budget (R048.1)."""
    answer = await engine_step.decide(port, "TaskPlan", params, run_ref=run_id)
    decision_id = str(answer.payload.get("decision_id") or answer.record_digest or "")
    if not decision_id:
        raise ConsumerRefused("budget_decision_unbound")
    return TrajectoryBudget(decision_id, int(answer.payload["count"]))


def apply_projection(
    pool: Sequence[str], payload: Mapping[str, Any]
) -> tuple[list[str], str | None]:
    """Apply the returned order, then reinsert the candidate at its position (R048.2)."""
    order = [str(item) for item in payload.get("order", pool)]
    candidate = payload.get("candidate")
    if candidate is not None:
        order.insert(int(payload.get("position", len(order))), str(candidate))
    return order, payload.get("projection_digest")


async def plan_breadth(
    port: EngineDecisionPort | None,
    params: Mapping[str, Any],
    *,
    run_id: str,
    max_concurrency: int,
) -> int:
    """Breadth for the next wave (R039.1, R048.3).

    Only ``NO_OUTCOME_HISTORY`` selects ``max_concurrency``; every other engine
    code passes through.
    """
    if port is None:
        raise EngineDecisionRefused("engine_decision_unavailable")
    answer = await port.decide("BranchOutcome", params, run_ref=run_id)
    if answer.refusal_code == "NO_OUTCOME_HISTORY":
        return max_concurrency
    if answer.refusal_code is not None:
        raise ConsumerRefused(answer.refusal_code)
    return int(answer.payload["breadth"])


async def run_wave(
    port: EngineDecisionPort | None,
    *,
    run_id: str,
    decision_id: str,
    breadth: int,
    run_branch: Callable[[int], Awaitable[Mapping[str, Any]]],
) -> list[Mapping[str, Any]]:
    """Start exactly ``breadth`` branches, log each outcome, return the wave receipt (R048.3)."""
    outcomes: list[Mapping[str, Any]] = []
    for index in range(breadth):
        outcome = await run_branch(index)
        await engine_step.record(port, "Learn", dict(outcome), run_ref=run_id)
        outcomes.append(outcome)
    receipt = {
        "decision_id": decision_id,
        "branch_count": breadth,
        "outcomes": outcomes,
    }
    await engine_step.record(port, "Learn", receipt, run_ref=run_id)
    return outcomes


async def run_pairwise(
    port: EngineDecisionPort | None,
    *,
    run_id: str,
    pairs: Sequence[tuple[str, str]],
    revision_bound: int,
    compare: Callable[[str, str], Awaitable[Mapping[str, Any]]],
    revise: Callable[[str], Awaitable[None]],
    revisions: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """Run each pair in order and send its verdict back; bound the revisions (R048.4)."""
    if len(revisions) > revision_bound:
        raise ConsumerRefused("revision_bound_exceeded")
    verdicts: list[dict[str, Any]] = []
    for left, right in pairs:
        verdict = dict(await compare(left, right))
        await engine_step.decide(
            port, "pairwise_verdict", {"pair": [left, right], **verdict}, run_ref=run_id
        )
        verdicts.append({"pair": [left, right], **verdict})
    for item in revisions:
        await revise(item)
    return verdicts


__all__ = [
    "TrajectoryBudget",
    "apply_projection",
    "bind_budget",
    "plan_breadth",
    "run_pairwise",
    "run_wave",
]
