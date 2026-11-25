"""Record the run's applied engine decisions as evaluations.

Run-end consumer step (MERISTEM-HARNESS-CYCLE-R004.1): for every engine
decision the run applied — stored on the state with
``GraphState.note_record`` under its question kind — the harness sends one
``Evaluate`` to the served engine decision port. The harness decides nothing
here: it maps the terminal status to the success value, calls the port once
per stored record digest and passes engine refusal codes through. A refused
evaluation never changes the run result.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from meristem import host_ports
from meristem.graph.state import GraphState

#: Terminal status to the success value of one evaluation; ``None`` (censored)
#: for a stopped run.
SUCCESS_BY_STATUS: Mapping[str, bool | None] = {
    "completed": True,
    "failed": False,
    "stopped": None,
}


def evaluation_id(run_id: str, kind: str) -> str:
    """One evaluation id per entry: a repeat of it is idempotent."""
    return f"{run_id}:{kind}"


async def report_outcomes(
    state: GraphState, ports: host_ports.HostPorts
) -> tuple[dict[str, Any], ...]:
    """Send one ``Evaluate`` per stored record digest; keep the run result.

    With no bound port the step refuses ``engine_decision_unavailable`` and
    decides nothing. Each entry returns its evidence mapping — ``record_id``,
    ``evaluation_id``, ``success`` and the answer's refusal code and record
    digest — and emits one run event carrying the same ``evaluation_id``.
    An engine refusal passes through in that evidence; it never changes the
    run result.
    """
    port = ports.engine_decision_port()
    if port is None:
        raise host_ports.EngineDecisionRefused("engine_decision_unavailable")
    if state.status not in SUCCESS_BY_STATUS:
        raise ValueError("outcomes require a terminal run status")
    success = SUCCESS_BY_STATUS[state.status]
    evidence: list[dict[str, Any]] = []
    for kind, record_id in state.engine_records.items():
        entry_id = evaluation_id(state.run_id, kind)
        answer = await port.log(
            "Evaluate",
            params={
                "record_id": record_id,
                "evaluation_id": entry_id,
                "success": success,
            },
            run_ref=state.run_id,
        )
        entry = {
            "record_id": record_id,
            "evaluation_id": entry_id,
            "success": success,
            "refusal_code": answer.refusal_code,
            "record_digest": answer.record_digest,
        }
        evidence.append(entry)
        ports.emit_run_event(
            {"type": "run_outcome_evaluated", "run_id": state.run_id, **entry}
        )
    return tuple(evidence)


__all__ = [
    "SUCCESS_BY_STATUS",
    "evaluation_id",
    "report_outcomes",
]
