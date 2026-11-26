"""The certification step submits the four-pin receipt and applies the attestation."""

from __future__ import annotations

import pytest
from support.recorded_engine import RecordedEnginePort

from meristem.harness.certification import submit_certification_receipt
from meristem.harness.run_lifecycle import ConsumerRefused

SPEC = "MERISTEM-HARNESS-CYCLE-R057"
PINS = {"skill": "s1", "model": "m1", "dataset": "d1", "criteria": "c1"}
BODY = {
    "op": "certify_local_skill",
    "request": {"tenant_id": "t", "pins": PINS, "criterion_ids": ["k1"]},
}


@pytest.mark.spec(SPEC)
async def test_receipt_submitted_and_attestation_applied():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "DecisionEval",
                    "params": BODY,
                    "answer": {
                        "outcome": "committed",
                        "payload": {"attestation_id": "att-9"},
                    },
                }
            ]
        }
    )
    attestation = await submit_certification_receipt(
        port,
        run_ref="r1",
        request={"tenant_id": "t"},
        pins=PINS,
        criterion_ids=["k1"],
    )
    assert attestation == "att-9"
    assert [call[0] for call in port.calls] == ["DecisionEval"]
    with pytest.raises(ConsumerRefused, match="CERTIFICATION_PIN_MISSING"):
        await submit_certification_receipt(
            port,
            run_ref="r1",
            request={},
            pins={**PINS, "dataset": ""},
            criterion_ids=[],
        )
