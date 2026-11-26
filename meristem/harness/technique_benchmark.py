"""Emit serving technique benchmark receipts (MERISTEM-HARNESS-CYCLE-R062).

For each technique the harness runs a baseline and a candidate on the same
model and configuration digest, then logs one receipt carrying both metrics
through ``DecisionLog``. It applies no verdict itself.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from meristem.harness import engine_step
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineDecisionPort

TECHNIQUES = (
    "paged_cache",
    "prefix_reuse",
    "fused_kernels",
    "quantized_cache",
    "expert_dispatch",
)

Measure = Callable[[str, str, str], Awaitable[Mapping[str, Any]]]
"""``(technique, arm, config_digest) -> metrics`` for one run of one arm."""


async def emit_technique_receipts(
    port: EngineDecisionPort | None,
    measure: Measure,
    *,
    run_ref: str,
    model_id: str,
    config_digest: str,
    techniques: tuple[str, ...] = TECHNIQUES,
) -> tuple[str, ...]:
    """Log one receipt per technique; return the techniques logged, in order."""
    unknown = [name for name in techniques if name not in TECHNIQUES]
    if unknown:
        raise ConsumerRefused("TECHNIQUE_UNKNOWN")
    logged: list[str] = []
    for technique in techniques:
        baseline = await measure(technique, "baseline", config_digest)
        candidate = await measure(technique, "candidate", config_digest)
        await engine_step.record(
            port,
            "Evaluate",
            {
                "technique": technique,
                "model_id": model_id,
                "config_digest": config_digest,
                "baseline": dict(baseline),
                "candidate": dict(candidate),
            },
            run_ref=run_ref,
        )
        logged.append(technique)
    return tuple(logged)


__all__ = ["TECHNIQUES", "Measure", "emit_technique_receipts"]
