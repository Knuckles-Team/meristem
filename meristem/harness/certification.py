"""Submit the local-model certification receipt (MERISTEM-HARNESS-CYCLE-R057).

The step builds the receipt with the four pins (skill, model, dataset,
criteria), submits it as the engine ``DecisionEval`` op ``certify_local_skill``
and applies the returned attestation id. It decides nothing and never opens the
normal invocation path.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from meristem.harness import engine_step
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineDecisionPort

PINS = ("skill", "model", "dataset", "criteria")


async def submit_certification_receipt(
    port: EngineDecisionPort | None,
    *,
    run_ref: str,
    request: Mapping[str, Any],
    pins: Mapping[str, str],
    criterion_ids: Sequence[str],
) -> str:
    """Submit the receipt and return the attestation id the engine issued."""
    if set(pins) != set(PINS) or not all(pins[name] for name in PINS):
        raise ConsumerRefused("CERTIFICATION_PIN_MISSING")
    body = {
        "op": "certify_local_skill",
        "request": {
            **request,
            "pins": dict(pins),
            "criterion_ids": list(criterion_ids),
        },
    }
    answer = await engine_step.write(port, "DecisionEval", body, run_ref=run_ref)
    attestation = answer.payload.get("attestation_id")
    if not isinstance(attestation, str) or not attestation:
        raise ConsumerRefused("CERTIFICATION_NOT_ATTESTED")
    return attestation


__all__ = ["PINS", "submit_certification_receipt"]
