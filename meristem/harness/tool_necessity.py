"""Compare each tool decision with the engine measure (MERISTEM-HARNESS-CYCLE-R020.1).

The comparison is advisory: a mismatch is recorded in the span and nothing else
changes. The engine holds the thresholds; the harness sends only what happened.
"""

from __future__ import annotations

from typing import Any

from meristem.harness import engine_step
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineDecisionPort


async def check_tool_necessity(
    port: EngineDecisionPort | None,
    *,
    run_id: str,
    tool_id: str,
    model_id: str,
    model_call_id: str,
    model_called: bool,
) -> dict[str, Any]:
    """Ask the engine whether the tool was necessary and return the span fields.

    An engine refusal never changes the run; its code is returned in the span.
    Without a bound port the step refuses ``engine_decision_unavailable``.
    """
    try:
        answer = await engine_step.decide(
            port,
            "tool_necessity",
            {
                "tool_id": tool_id,
                "model_id": model_id,
                "model_call_id": model_call_id,
                "model_called": model_called,
            },
            run_ref=run_id,
        )
    except ConsumerRefused as refused:
        return {"model_call_id": model_call_id, "refusal_code": refused.code}
    payload = answer.payload
    return {
        "model_call_id": model_call_id,
        "necessity": payload.get("necessity"),
        "agreement": payload.get("agreement"),
        "mismatch": payload.get("mismatch"),
    }


__all__ = ["check_tool_necessity"]
