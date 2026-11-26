"""Apply the received historical impact risk ordering (MERISTEM-HARNESS-CYCLE-R065).

The change-planning step reads ``PROPAGATE ... RISK HISTORY`` through the
served ``Uql`` read, applies the ordering the answer carries, computes no risk
and holds no threshold. Nodes the engine reports ``unknown`` stay in review.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from meristem.harness import engine_step
from meristem.host_ports import EngineDecisionPort


@dataclass(frozen=True, slots=True)
class RiskOrder:
    """Steps in received risk order plus the nodes held for review."""

    ordered: tuple[str, ...]
    review: tuple[str, ...]


def order_by_historical_risk(
    steps: Sequence[str], risk: Mapping[str, Any]
) -> RiskOrder:
    """Order ``steps`` by the received ``ordering``; route ``unknown`` to review."""
    unknown = set(risk.get("unknown") or ())
    rank = {node: index for index, node in enumerate(risk.get("ordering") or ())}
    review = tuple(step for step in steps if step in unknown or step not in rank)
    ranked = sorted((s for s in steps if s in rank and s not in unknown), key=rank.get)  # type: ignore[arg-type]
    return RiskOrder(ordered=tuple(ranked), review=review)


async def request_historical_risk(
    port: EngineDecisionPort | None,
    query: str,
    steps: Sequence[str],
    *,
    run_ref: str,
) -> RiskOrder:
    """Send the ``RISK HISTORY`` query and apply the received ordering."""
    answer = await engine_step.read(
        port, "UnifiedQuery", {"uql": query}, run_ref=run_ref
    )
    return order_by_historical_risk(steps, answer.payload)


__all__ = ["RiskOrder", "order_by_historical_risk", "request_historical_risk"]
