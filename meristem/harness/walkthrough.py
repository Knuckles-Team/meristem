"""Produce the code walkthrough manifest for a run (MERISTEM-HARNESS-CYCLE-R073).

The manifest covers source-pinned sections and binds execution spans and step
indexes from the run events. It travels through the served ``add_node`` write;
the engine refuses a stale source revision.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from meristem.harness import engine_step
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineDecisionPort


def build_walkthrough_manifest(
    sections: Sequence[Mapping[str, Any]], events: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Bind each source-pinned section to the spans and step indexes of ``events``."""
    notes = []
    for section in sections:
        if not section.get("source_revision") or not section.get("path"):
            raise ConsumerRefused("WALKTHROUGH_SOURCE_UNPINNED")
        bound = [
            {"span_id": e.get("span_id") or e.get("task_id"), "step": index}
            for index, e in enumerate(events)
            if e.get("path") == section["path"]
        ]
        notes.append({**section, "spans": bound})
    return {"sections": notes}


async def send_walkthrough_manifest(
    port: EngineDecisionPort | None, manifest: Mapping[str, Any], *, run_ref: str
) -> str | None:
    """Send the manifest; the engine's refusal (a stale source) passes through."""
    answer = await engine_step.write(
        port,
        "AgentComponent",
        {"op": "add_node", "node": dict(manifest)},
        run_ref=run_ref,
    )
    return answer.record_digest


__all__ = ["build_walkthrough_manifest", "send_walkthrough_manifest"]
