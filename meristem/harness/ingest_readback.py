"""Report ingest success only after a matched readback (MERISTEM-HARNESS-CYCLE-R075).

A run that ingests content asks the served ``VerifyIngestReadback`` and counts
as a success only when the receipt verdict is ``matched``. Any other verdict
makes the run non-success with the engine code.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from meristem.harness import engine_step
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineDecisionPort


async def verify_ingest_readback(
    port: EngineDecisionPort | None, request: Mapping[str, Any], *, run_ref: str
) -> Mapping[str, Any]:
    """Return the matched receipt; refuse with the engine code otherwise."""
    answer = await engine_step.read(
        port, "VerifyIngestReadback", dict(request), run_ref=run_ref
    )
    receipt = answer.payload
    if receipt.get("verdict") != "matched":
        raise ConsumerRefused(
            answer.refusal_code or str(receipt.get("code") or "READBACK_MISMATCHED")
        )
    return receipt


__all__ = ["verify_ingest_readback"]
