"""The flag tool and the friction tool, bound in every run (R093)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from typing import Any

FLAG_TOOL = "knowledge_flag"
FRICTION_TOOL = "skills_friction_report"
TOOL_OPERATIONS: tuple[tuple[str, str], ...] = (
    (FLAG_TOOL, "knowledge.flag.raise"),
    (FRICTION_TOOL, "skills.friction.report"),
)
_TOOL_NAMES = tuple(name for name, _ in TOOL_OPERATIONS)
FLAG_TOOL_UNBOUND = "FLAG_TOOL_UNBOUND"

#: ``(operation_id, arguments) -> answer`` through the arbora client.
OperationCaller = Callable[[str, Mapping[str, Any]], Awaitable[Mapping[str, Any]]]


class FlagToolUnbound(RuntimeError):
    """A plan lacks one of the two tools; the run ends failed at plan time."""

    code = FLAG_TOOL_UNBOUND


def bind_knowledge_tools(plan: Any, ports: Any) -> Any:
    """Add both tools to the plan's tool set, even under ``allowed_tools``.

    A plan with a received text-only constraint carries no tools, and a run
    whose host binds no native toolset has nothing to bind through; both stay
    unchanged. Otherwise the host must serve both tools or the plan refuses
    with ``FLAG_TOOL_UNBOUND`` before the run starts.
    """
    if plan.execution_constraint == "text-only" or ports.native is None:
        return plan
    require_knowledge_tools(tuple(ports.native_tool_names()))
    names = tuple(plan.tool_names)
    missing = tuple(name for name in _TOOL_NAMES if name not in names)
    return replace(plan, tool_names=names + missing) if missing else plan


def require_knowledge_tools(tool_names: tuple[str, ...]) -> None:
    """Refuse a tool set that lacks either tool."""
    if any(name not in tool_names for name in _TOOL_NAMES):
        raise FlagToolUnbound(FLAG_TOOL_UNBOUND)


def build_knowledge_tools(
    call: OperationCaller, *, run_id: str, trace_node_id: Callable[[], str]
) -> dict[str, Callable[..., Awaitable[Any]]]:
    """The two tool callables; each adds ``run_id`` and the current node id."""

    def make(tool: str) -> Callable[..., Awaitable[Any]]:
        async def run(
            target: str, kind: str, observation: str, correction: str | None = None
        ) -> Any:
            args: dict[str, Any] = {
                "target": target,
                "kind": kind,
                "observation": observation,
                "run_id": run_id,
                "trace_node_id": trace_node_id(),
            }
            if correction is not None:
                args["correction"] = correction
            answer = await call(dict(TOOL_OPERATIONS)[tool], args)
            return answer.get("flag_id")

        return run

    return {name: make(name) for name in _TOOL_NAMES}


__all__ = [
    "FLAG_TOOL",
    "FLAG_TOOL_UNBOUND",
    "FRICTION_TOOL",
    "TOOL_OPERATIONS",
    "FlagToolUnbound",
    "bind_knowledge_tools",
    "build_knowledge_tools",
    "require_knowledge_tools",
]
