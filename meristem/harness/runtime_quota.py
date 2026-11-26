"""Hand the engine's received quota values to the workspace backend (MERISTEM-HARNESS-CYCLE-R016.1).

The harness holds no quota threshold and never raises a value: the four options
come from one ``runtime_quota`` decision and become the backend's limits before
the workspace starts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from meristem.harness import engine_step
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineDecisionPort

OPTIONS = ("wall_time_ms", "memory_bytes", "process_count", "grace_ms")


@dataclass(frozen=True, slots=True)
class RuntimeQuota:
    """The four received limits and the record digest they came with."""

    wall_time_ms: int
    memory_bytes: int
    process_count: int
    grace_ms: int
    record_digest: str | None


async def apply_runtime_quota(
    workspace: Any, port: EngineDecisionPort | None, task_class: str
) -> RuntimeQuota:
    """Decide the quota once and set it as the backend's ``limits``.

    A missing option refuses ``quota_option_missing``; ``QUOTA_ABOVE_CEILING``
    and other engine codes pass through. Call before ``DevWorkspace.start``.
    """
    answer = await engine_step.decide(
        port, "runtime_quota", {"task_class": task_class}, run_ref=workspace.run_id
    )
    payload = answer.payload
    if any(type(payload.get(name)) is not int for name in OPTIONS):
        raise ConsumerRefused("quota_option_missing")
    quota = RuntimeQuota(
        wall_time_ms=int(payload["wall_time_ms"]),
        memory_bytes=int(payload["memory_bytes"]),
        process_count=int(payload["process_count"]),
        grace_ms=int(payload["grace_ms"]),
        record_digest=answer.record_digest,
    )
    workspace.backend.limits = quota
    return quota


__all__ = ["OPTIONS", "RuntimeQuota", "apply_runtime_quota"]
