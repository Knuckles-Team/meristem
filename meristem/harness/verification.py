"""Replicated steps, trajectory join and step verification (MERISTEM-HARNESS-CYCLE-R029, R030).

The harness measures windows, runs what the engine names and reports each result;
it never decides drift, width, winner or admission.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from meristem.harness import engine_step
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineDecisionPort


async def run_replicas(
    port: EngineDecisionPort | None,
    *,
    run_id: str,
    reference_window: Sequence[str],
    current_window: Sequence[str],
    diversity_inputs: Mapping[str, Any],
    replica_ceiling: int,
    run_replica: Callable[[int], Awaitable[Any]],
    irreversible: bool = False,
) -> dict[str, Any]:
    """Run replicas at the engine's width and honour the autonomy verdict (R029.1)."""
    windows = {
        "reference_digest": engine_step.digest_of(list(reference_window)),
        "current_digest": engine_step.digest_of(list(current_window)),
    }
    await engine_step.decide(port, "population_drift", windows, run_ref=run_id)
    width_answer = await engine_step.decide(
        port, "fanout_width", dict(diversity_inputs), run_ref=run_id
    )
    width = int(width_answer.payload["width"])
    if width > replica_ceiling:
        raise ConsumerRefused("replica_width_above_ceiling")
    verdict = await engine_step.decide(port, "autonomy_verdict", {}, run_ref=run_id)
    autonomous = verdict.payload.get("verdict") == "autonomous"
    if irreversible and not autonomous:
        return {**windows, "width": width, "verdict": "confirm", "results": []}
    results = [await run_replica(index) for index in range(width)]
    return {
        **windows,
        "width": width,
        "verdict": verdict.payload.get("verdict"),
        "results": results,
    }


async def report_checks(
    port: EngineDecisionPort | None,
    *,
    run_id: str,
    trajectories: Sequence[Mapping[str, Any]],
    gates: Sequence[Mapping[str, Any]],
    probes: Sequence[Mapping[str, Any]],
    named_probes: Sequence[str],
) -> dict[str, Any]:
    """Report each trajectory, gate and probe result; apply the answers as returned (R030.1)."""
    if any(probe.get("id") not in named_probes for probe in probes):
        raise ConsumerRefused("probe_budget_exceeded")
    join = await engine_step.decide(
        port, "trajectory_join", {"trajectories": list(trajectories)}, run_ref=run_id
    )
    gate = await engine_step.decide(
        port, "step_verification", {"gates": list(gates)}, run_ref=run_id
    )
    admission = await engine_step.decide(
        port, "adversarial_admission", {"probes": list(probes)}, run_ref=run_id
    )
    return {
        "winner": join.payload.get("winner"),
        "recovery": gate.payload.get("recovery"),
        "admission": admission.payload.get("admitted"),
    }


__all__ = ["report_checks", "run_replicas"]
