"""Consume n-ary hypotheses as proposals only (MERISTEM-HARNESS-CYCLE-R066).

Each tuple ``GraphLearnPredictNary`` returns becomes a proposal that keeps its
evidence class and revisions. The harness never asserts a tuple as a fact and
never writes one to the graph.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from meristem.harness import engine_step
from meristem.host_ports import EngineDecisionPort


@dataclass(frozen=True, slots=True)
class Proposal:
    """One hypothesis, marked as a proposal and never as a fact."""

    tuple_: Mapping[str, Any]
    evidence_class: str | None
    revisions: Mapping[str, Any]
    asserted: bool = False


async def read_nary_proposals(
    port: EngineDecisionPort | None, params: Mapping[str, Any], *, run_ref: str
) -> tuple[Proposal, ...]:
    """Read hypotheses through the host port and wrap each as a proposal."""
    answer = await engine_step.read(
        port, "GraphLearnPredictNary", dict(params), run_ref=run_ref
    )
    revisions = {
        key: answer.payload[key]
        for key in ("graph_revision", "model_revision")
        if key in answer.payload
    }
    return tuple(
        Proposal(
            tuple_=dict(item),
            evidence_class=item.get("evidence_class"),
            revisions=revisions,
        )
        for item in answer.payload.get("hypotheses") or ()
    )


__all__ = ["Proposal", "read_nary_proposals"]
