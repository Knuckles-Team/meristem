"""The champion skill consumer: one pointer read, one received revision.

The run admits the champion only through the engine's served pointer:
one read under ``champion:program:<program_ref>`` through the bound
engine decision port, and the harness loads exactly the returned
revision reference. It never picks a revision locally; a fallback
default exists only for the absence of the decision. Every engine call
goes through the engine decision port.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import HostPorts

#: The pointer key prefix of one program's champion.
CHAMPION_POINTER_PREFIX = "champion:program:"


def champion_pointer_key(program_ref: str) -> str:
    """The pointer key that serves one program's champion revision."""
    return f"champion:program:{program_ref}"


@dataclass(frozen=True, slots=True)
class ChampionRevision:
    """One champion pointer read: the key asked and the revision served."""

    pointer_key: str
    revision_ref: str


@dataclass
class ChampionApplied:
    """One run's champion application, held across the on_start point.

    ``revision`` is the received pointer answer; ``instructions`` is the
    champion revision applied to the run plan's received instructions.
    """

    revision: ChampionRevision | None = None
    instructions: str | None = None


async def apply_champion_skill(
    program_ref: str,
    instructions: str,
    *,
    ports: HostPorts,
    run_ref: str,
) -> tuple[ChampionRevision, str]:
    """Apply the received champion skill revision to the run's instructions.

    One pointer read under ``champion:program:<program_ref>`` through
    the bound engine decision port's read arm. The harness loads
    exactly the returned ``revision_ref`` and never picks a revision
    locally; the pointer key and the revision enter the run trace.
    Without a bound port the step refuses
    ``engine_decision_unavailable`` and decides nothing; engine codes
    ``PROMOTION_NO_BASELINE`` and ``ROLLBACK_NO_PARENT`` pass through;
    an absent pointer refuses ``champion_missing``.
    """
    pointer_key = champion_pointer_key(program_ref)
    answer = await ports.engine_read(
        "pointer", {"pointer_key": pointer_key}, run_ref=run_ref
    )
    if answer.refusal_code is not None:
        raise ConsumerRefused(answer.refusal_code)
    if answer.outcome not in ("solved", "committed"):
        raise ConsumerRefused("champion_missing")
    revision_ref = str(answer.payload.get("revision_ref") or "")
    if not revision_ref:
        raise ConsumerRefused("champion_missing")
    content = answer.payload.get("instructions")
    applied = str(content) if content is not None else instructions
    return ChampionRevision(pointer_key=pointer_key, revision_ref=revision_ref), applied


def champion_on_start(applied: ChampionApplied):
    """The run's ``on_start`` step: read the pointer and apply the revision.

    The program reference rides on the request's received
    ``harness_program_ref`` metadata; a run without one has no program
    to champion and the step decides nothing. On a received revision
    the pointer key and the revision enter the run trace through one
    run event, and the applied instructions replace the plan's.
    """

    async def step(request: Any, plan: Any) -> None:
        program_ref = str(request.metadata.get("harness_program_ref") or "")
        if not program_ref:
            return
        revision, applied_instructions = await apply_champion_skill(
            program_ref,
            plan.instructions,
            ports=request.ports,
            run_ref=request.run_id,
        )
        applied.revision = revision
        applied.instructions = applied_instructions
        request.ports.emit_run_event(
            {
                "type": "champion_skill",
                "pointer_key": revision.pointer_key,
                "revision_ref": revision.revision_ref,
            }
        )

    return step


__all__ = [
    "CHAMPION_POINTER_PREFIX",
    "ChampionApplied",
    "ChampionRevision",
    "apply_champion_skill",
    "champion_on_start",
    "champion_pointer_key",
]
