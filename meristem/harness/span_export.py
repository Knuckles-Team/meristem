"""Export one span per agent step and per tool call of a run (MERISTEM-HARNESS-CYCLE-R070).

The spans are read from the run events the loop already emits; there is no
second tracer. Each span carries its parent run id and its child agent or tool
call id, and travels as a ``RunTrace`` node through the served ``add_node``
write. The harness decides nothing and sends every span or refuses.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from meristem.harness import engine_step
from meristem.host_ports import EngineDecisionPort

TERMINAL_KINDS = frozenset({"task_completed", "task_failed"})


def build_spans(
    run_id: str,
    events: Sequence[Mapping[str, Any]],
    tool_trace: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    """One span per agent step event and per recorded tool step of the run.

    The output tool's terminal event follows the finish gate, so tool spans
    come from the run's tool trace, which is complete when the gate runs.
    """
    spans: list[dict[str, Any]] = []
    for event in events:
        if event.get("type") not in TERMINAL_KINDS or event.get("run_id") != run_id:
            continue
        if "tool_step" in event:
            continue
        is_tool = False
        task_id = event["task_id"]
        spans.append(
            {
                "span_id": event["event_id"],
                "run_id": run_id,
                "parent_run_id": event.get("parent_run_id", run_id),
                "kind": "tool" if is_tool else "agent",
                "tool_call_id": task_id if is_tool else None,
                "child_agent": None if is_tool else task_id,
                "name": event.get("tool") if is_tool else task_id,
                "outcome": event.get("status", "failed"),
                "sequence": event["sequence"],
            }
        )
    for index, step in enumerate(tool_trace):
        spans.append(
            {
                "span_id": f"{run_id}:tool:{index}",
                "run_id": run_id,
                "parent_run_id": run_id,
                "kind": "tool",
                "tool_call_id": step["task_id"],
                "child_agent": None,
                "name": step["name"],
                "outcome": step["outcome"],
                "sequence": index,
            }
        )
    return spans


async def export_spans(
    port: EngineDecisionPort | None, spans: Sequence[Mapping[str, Any]], *, run_ref: str
) -> tuple[str | None, ...]:
    """Send every span as a ``RunTrace`` node; return the record digests in order."""
    digests = []
    for span in spans:
        answer = await engine_step.write(
            port,
            "AgentComponent",
            {"op": "add_node", "node": {"label": "RunTrace", "properties": dict(span)}},
            run_ref=run_ref,
        )
        digests.append(answer.record_digest)
    return tuple(digests)


__all__ = ["build_spans", "export_spans"]
