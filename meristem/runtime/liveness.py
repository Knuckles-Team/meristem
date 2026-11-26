"""The sandbox liveness monitor (MERISTEM-HARNESS-CYCLE-R063).

The monitor logs liveness evidence for each process bound to one run through
the engine ``DecisionLog`` ``Evaluate`` write, once per received interval, and
applies a ``terminate`` verdict the engine returns. ``terminate`` kills exactly
one process, only after the host admits it, and refuses a run or process
mismatch. The wait between evidence rounds is the host's injected clock or
event; the monitor never sleeps and decides nothing.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from meristem.host_ports import LivenessWait, PlanConfirmer, SandboxProcess

#: Logs one evidence record through ``DecisionLog`` and returns the engine payload.
EvidenceLog = Callable[[Mapping[str, Any]], Awaitable[Mapping[str, Any]]]


class LivenessRefused(RuntimeError):
    """The monitor refused with a closed code; the run fails closed with it."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


#: ``knob harness.liveness_interval``: fallback and closed range, in seconds.
DEFAULT_INTERVAL_S = 30.0
MIN_INTERVAL_S = 5.0
MAX_INTERVAL_S = 600.0


def liveness_interval(received: Any) -> float:
    """The received interval in seconds; the fallback when absent or out of range."""
    if isinstance(received, bool) or not isinstance(received, int | float):
        return DEFAULT_INTERVAL_S
    if not MIN_INTERVAL_S <= received <= MAX_INTERVAL_S:
        return DEFAULT_INTERVAL_S
    return float(received)


class LivenessMonitor:
    """Liveness evidence and ``terminate`` for the processes bound to one run."""

    def __init__(
        self,
        log: EvidenceLog,
        confirm: PlanConfirmer | None,
        processes: Sequence[SandboxProcess],
        *,
        run_id: str,
        interval_s: float,
    ) -> None:
        self._log = log
        self._confirm = confirm
        self._bound: dict[str, SandboxProcess] = {p.process_id: p for p in processes}
        self.run_id = run_id
        self.interval_s = interval_s
        self.refusal: LivenessRefused | None = None

    async def evidence_once(self) -> None:
        """Log one evidence record per bound process and apply a terminate verdict."""
        for process_id, process in list(self._bound.items()):
            verdict = await self._log(
                {
                    "kind": "liveness",
                    "run_id": self.run_id,
                    "process_id": process_id,
                    "alive": await process.alive(),
                }
            )
            if verdict.get("verdict") == "terminate":
                await self.terminate(self.run_id, verdict.get("process_id", process_id))

    async def terminate(self, run_id: str, process_id: str) -> None:
        """Kill exactly ``process_id`` of this run once the host admits it."""
        process = self._bound.get(process_id)
        if run_id != self.run_id or process is None:
            raise LivenessRefused("PROCESS_MISMATCH")
        if self._confirm is None:
            raise LivenessRefused("PROCESS_TERMINATE_CONFIRM_UNAVAILABLE")
        try:
            answer = await self._confirm(
                "process_terminate",
                {
                    "confirmation_kind": "process_terminate",
                    "run_id": run_id,
                    "process_id": process_id,
                },
                run_id,
            )
        except Exception as exc:  # noqa: BLE001 - any refusal is a deny
            raise LivenessRefused("PROCESS_TERMINATE_DENIED") from exc
        if answer.refusal_code is not None or answer.verdict != "admit":
            raise LivenessRefused(answer.refusal_code or "PROCESS_TERMINATE_DENIED")
        del self._bound[process_id]
        await process.kill()

    async def watch(self, wait: LivenessWait) -> None:
        """Log evidence each time the injected ``wait`` returns, until cancelled."""
        try:
            while True:
                await wait(self.interval_s)
                await self.evidence_once()
        except LivenessRefused as refusal:
            self.refusal = refusal  # the run fails closed at its finish gate
        except Exception as exc:  # noqa: BLE001 - a failed round is a refusal
            self.refusal = LivenessRefused(type(exc).__name__)


__all__ = [
    "EvidenceLog",
    "LivenessRefused",
    "DEFAULT_INTERVAL_S",
    "MAX_INTERVAL_S",
    "MIN_INTERVAL_S",
    "LivenessMonitor",
    "liveness_interval",
]
