"""Staff a run from served recruit and allocate answers and advance instance state (MERISTEM-HARNESS-CYCLE-R074).

The step asks the served ``Decide`` with ``recruit`` and then ``allocate``,
applies the answers as received, and advances the instance through the served
``Statechart`` write. It decides nothing: the staffing is exactly what the
engine answered, and an engine refusal passes through as a run refusal.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from meristem.harness import engine_step
from meristem.host_ports import EngineDecisionPort


@dataclass(frozen=True, slots=True)
class Staffing:
    """The recruited candidates and the allocation, as the engine answered."""

    recruited: tuple[str, ...]
    allocation: Mapping[str, Any]


async def staff_run(
    port: EngineDecisionPort | None,
    recruit: Mapping[str, Any],
    allocate: Mapping[str, Any],
    *,
    run_ref: str,
) -> Staffing:
    """Ask ``recruit``, then ``allocate`` over the recruited candidates."""
    recruited = await engine_step.decide(port, "recruit", recruit, run_ref=run_ref)
    candidates = list(recruited.option_ids)
    allocated = await engine_step.decide(
        port, "allocate", {**allocate, "candidates": candidates}, run_ref=run_ref
    )
    return Staffing(tuple(candidates), dict(allocated.payload))


async def advance_instance(
    port: EngineDecisionPort | None, instance_id: str, event: str, *, run_ref: str
) -> str | None:
    """Advance one instance through ``Statechart``; return the state the engine reports."""
    answer = await engine_step.write(
        port,
        "Statechart",
        {"instance_id": instance_id, "event": event},
        run_ref=run_ref,
    )
    state = answer.payload.get("state")
    return state if isinstance(state, str) else None


__all__ = ["Staffing", "advance_instance", "staff_run"]
