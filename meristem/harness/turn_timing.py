"""Record client-monotonic elapsed time per turn (MERISTEM-HARNESS-CYCLE-R061).

Request and run elapsed times come from a monotonic clock and travel with the
turn receipt reference through ``DecisionLog``. They are never labelled provider
generation time or token rate. A clock failure omits the observation.
"""

from __future__ import annotations

from collections.abc import Callable
from time import perf_counter_ns
from typing import Any

from meristem.harness import engine_step
from meristem.host_ports import EngineDecisionPort


async def record_turn_timing(
    port: EngineDecisionPort | None,
    *,
    run_ref: str,
    receipt_ref: str,
    request_started_ns: int,
    run_started_ns: int,
    clock: Callable[[], int] = perf_counter_ns,
) -> bool:
    """Send both elapsed values with the receipt reference; ``False`` if omitted."""
    try:
        now = clock()
    except Exception:  # noqa: BLE001 - a clock failure omits the observation
        return False
    body: dict[str, Any] = {
        "receipt_ref": receipt_ref,
        "client_request_elapsed_ms": max(0, (now - request_started_ns) // 1_000_000),
        "client_run_elapsed_ms": max(0, (now - run_started_ns) // 1_000_000),
        "clock": "client_monotonic",
    }
    await engine_step.record(port, "Evaluate", body, run_ref=run_ref)
    return True


__all__ = ["record_turn_timing"]
