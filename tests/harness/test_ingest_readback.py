"""An ingesting run succeeds only after a matched readback."""

from __future__ import annotations

import pytest
from support.recorded_engine import RecordedEnginePort

from meristem.harness.ingest_readback import verify_ingest_readback
from meristem.harness.run_lifecycle import ConsumerRefused

SPEC = "MERISTEM-HARNESS-CYCLE-R075"
REQUEST = {
    "expected_graph_revision": 4,
    "write_receipt_digest": "w" * 64,
    "results": [],
}


def port_with(answer):
    return RecordedEnginePort(
        {
            "answers": [
                {"method": "VerifyIngestReadback", "params": REQUEST, "answer": answer}
            ]
        }
    )


@pytest.mark.spec(SPEC)
async def test_success_only_after_matched_readback():
    receipt = await verify_ingest_readback(
        port_with({"outcome": "solved", "payload": {"verdict": "matched"}}),
        REQUEST,
        run_ref="r1",
    )
    assert receipt["verdict"] == "matched"
    with pytest.raises(ConsumerRefused, match="READBACK_MISMATCHED"):
        await verify_ingest_readback(
            port_with({"outcome": "solved", "payload": {"verdict": "mismatched"}}),
            REQUEST,
            run_ref="r1",
        )
    with pytest.raises(ConsumerRefused, match="GRAPH_REVISION_STALE"):
        await verify_ingest_readback(
            port_with({"outcome": "abstained", "refusal_code": "GRAPH_REVISION_STALE"}),
            REQUEST,
            run_ref="r1",
        )
