"""The one designation apply of the run loop (MERISTEM-HARNESS-CYCLE-R013).

The engine designates the tools, agents and skills a run may use. The harness
replaces the plan's tool names with the designated tools and carries the agent
and skill names as given. It never ranks, adds or drops an entry.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, replace
from typing import Any

from meristem.harness import engine_step
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineDecisionPort

KIND = "capability_designation"


@dataclass(frozen=True, slots=True)
class Designation:
    """The designated entries with the engine's reason and record digest."""

    tools: tuple[str, ...]
    agents: tuple[str, ...]
    skills: tuple[str, ...]
    why: Mapping[str, str]
    record_digest: str | None


def _ids(entries: Any) -> tuple[tuple[str, ...], dict[str, str]]:
    ids: list[str] = []
    why: dict[str, str] = {}
    for entry in entries or ():
        name = entry["id"] if isinstance(entry, Mapping) else entry
        ids.append(str(name))
        if isinstance(entry, Mapping):
            why[str(name)] = str(entry.get("why", ""))
    return tuple(ids), why


async def apply_designation(
    request: Any,
    plan: Any,
    port: EngineDecisionPort | None,
    *,
    task_embedding_ref: str,
    required_capabilities: Collection[str],
    bound_tools: Collection[str] | None = None,
) -> tuple[Any, Designation]:
    """Ask the designation once and replace ``plan.tool_names`` with its tools.

    A designated tool the host has not bound refuses
    ``designation_unknown_tool``; engine codes pass through.
    """
    answer = await engine_step.decide(
        port,
        KIND,
        {
            "task_embedding_ref": task_embedding_ref,
            "required_capabilities": sorted(required_capabilities),
        },
        run_ref=request.run_id,
    )
    tools, why = _ids(answer.payload.get("tools"))
    agents, agent_why = _ids(answer.payload.get("agents"))
    skills, skill_why = _ids(answer.payload.get("skills"))
    if bound_tools is not None and any(tool not in bound_tools for tool in tools):
        raise ConsumerRefused("designation_unknown_tool")
    designation = Designation(
        tools, agents, skills, {**why, **agent_why, **skill_why}, answer.record_digest
    )
    return replace(plan, tool_names=tools), designation


async def report_capability_outcomes(
    state: Any,
    port: EngineDecisionPort | None,
    designation: Designation,
    ran: Mapping[str, bool | None],
) -> list[dict[str, Any]]:
    """Log one outcome per designated entry (MERISTEM-HARNESS-CYCLE-R013.2).

    ``ran`` maps an entry to ``True`` (ran without error), ``False`` (error) or
    ``None`` (never ran). An engine refusal never changes the run result.
    """
    trace: list[dict[str, Any]] = []
    for capability in (*designation.tools, *designation.agents, *designation.skills):
        body = {
            "designation_digest": designation.record_digest,
            "capability_id": capability,
            "success": ran.get(capability),
        }
        try:
            await engine_step.record(
                port, "capability_outcome", body, run_ref=state.run_id
            )
            refusal = None
        except ConsumerRefused as refused:
            refusal = refused.code
        trace.append({**body, "refusal_code": refusal})
    return trace


__all__ = ["Designation", "apply_designation", "report_capability_outcomes"]
