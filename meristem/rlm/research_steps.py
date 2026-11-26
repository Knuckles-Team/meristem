"""The engine-decided next read of one recursive research run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineDecisionRefused

if TYPE_CHECKING:
    from .runner import _Env

#: Engine refusal codes that end the run and pass through unchanged.
RUN_ENDING_CODES = ("RESEARCH_DEPTH_EXCEEDED", "RESEARCH_NO_BUDGET")


@dataclass(frozen=True, slots=True)
class ResearchStep:
    """One received research step: read the returned source, or stop."""

    action: str
    source: str | None
    reason: str
    decision_id: str | None


async def next_read(env: _Env) -> ResearchStep:
    """Apply the engine's next-read and stop decision before each read.

    The decision goes to one ``ResearchNext`` decision; when the answer
    says stop the run ends with its current result, and the engine codes
    ``RESEARCH_DEPTH_EXCEEDED`` and ``RESEARCH_NO_BUDGET`` end the run
    and pass through unchanged. The decision id enters the
    ``research_next`` lineage event. An unbound port refuses
    ``engine_decision_unavailable`` and decides nothing; there is no
    write path.
    """
    engine = env.ports.engine_decision_port()
    if engine is None:
        raise EngineDecisionRefused("engine_decision_unavailable")
    run_ref = env.lineage.run_id if env.lineage is not None else "rlm"
    answer = await engine.decide("ResearchNext", {}, run_ref=run_ref)
    if answer.refusal_code is not None:
        if answer.refusal_code in RUN_ENDING_CODES:
            return ResearchStep("stop", None, answer.refusal_code, answer.record_digest)
        raise ConsumerRefused(answer.refusal_code)
    if answer.outcome not in ("solved", "committed"):
        raise ConsumerRefused("research_next_unserved")
    payload = answer.payload
    if payload.get("stop") is True:
        # Deferred: runner imports this module at its top level.
        from .runner import _emit_lineage

        _emit_lineage(env, "research_next", decision_id=answer.record_digest)
        return ResearchStep(
            "stop",
            None,
            str(payload.get("reason") or "stopped"),
            answer.record_digest,
        )
    source = payload.get("source")
    if not isinstance(source, str) or not source:
        raise ConsumerRefused("research_next_unserved")
    from .runner import _emit_lineage

    _emit_lineage(env, "research_next", decision_id=answer.record_digest, source=source)
    return ResearchStep("read", source, "read", answer.record_digest)


__all__ = ["ResearchStep", "next_read"]
