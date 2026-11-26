"""The retrieval step applies received verdicts and recomputes none (MERISTEM-RUN-R010, R011).

The step sends its request through the host tool caller and applies the
sufficiency repair, the stop verdict and the arbitration choice as received.
It holds no threshold.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import HostPorts, ToolStepResult

CONNECTOR = "medulla"


@dataclass(frozen=True, slots=True)
class RetrievalOutcome:
    """What one retrieval round returned and what the step applies."""

    repair: Any = None
    stop: bool = False
    choice: Any = None
    abstained: bool = False
    candidate: Any = None


@dataclass(frozen=True, slots=True)
class DiscoverReport:
    """The received Discover verdict, reported to the caller unchanged."""

    gate_passed: bool
    failure_modes: tuple[str, ...] = field(default=())


async def _call(
    ports: HostPorts, tool: str, arguments: Mapping[str, Any], task_id: str
) -> Any:
    caller = ports.tool_caller()
    if caller is None:
        raise ConsumerRefused("tool_caller_unavailable")
    raw = await caller(CONNECTOR, tool, dict(arguments), task_id)
    result = raw if isinstance(raw, ToolStepResult) else ToolStepResult(result=raw)
    if result.outcome != "ok":
        raise ConsumerRefused(result.error or "retrieval_call_refused")
    return result.result


async def run_retrieval_round(
    ports: HostPorts,
    *,
    plan: Mapping[str, Any],
    counters: Mapping[str, int],
    prior: Any = None,
    run_id: str,
) -> RetrievalOutcome:
    """Send one ``RetrievalRound`` and apply its verdicts (R010).

    ``SufficiencyVerdict.repair``, ``StopVerdict.stop`` and
    ``ArbitrationVerdict.choice`` are applied as received. An ``Abstain`` choice
    returns the typed abstention to the caller.
    """
    answer = await _call(
        ports,
        "eg.query.RetrievalRound",
        {
            "request": {
                "plan": dict(plan),
                "state": dict(counters),
                **({"arbitration": prior} if prior is not None else {}),
            }
        },
        f"{run_id}:retrieval",
    )
    choice = (answer.get("arbitration") or {}).get("choice")
    return RetrievalOutcome(
        repair=(answer.get("sufficiency") or {}).get("repair"),
        stop=bool((answer.get("stop") or {}).get("stop")),
        choice=choice,
        abstained=choice == "Abstain",
        candidate=answer.get("candidate"),
    )


async def read_discover_verdict(
    ports: HostPorts, *, request: Mapping[str, Any], run_id: str
) -> DiscoverReport:
    """Read ``DiscoverResult.verdict`` and report it unchanged (R011).

    ``gate_passed`` false means the rows are low-quality evidence; the failure
    modes go to the caller as received and no mode or threshold is recomputed.
    """
    answer = await _call(ports, "eg.ingestion.Discover", request, f"{run_id}:discover")
    verdict = answer.get("verdict") or {}
    return DiscoverReport(
        gate_passed=bool(verdict.get("gate_passed")),
        failure_modes=tuple(str(m) for m in verdict.get("failure_modes", ())),
    )


__all__ = [
    "DiscoverReport",
    "RetrievalOutcome",
    "read_discover_verdict",
    "run_retrieval_round",
]
