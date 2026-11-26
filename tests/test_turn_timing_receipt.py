"""Turn end records client-monotonic elapsed time with the receipt reference."""

from __future__ import annotations

import pytest
from support.recorded_engine import RecordedEnginePort

from meristem.harness.turn_timing import record_turn_timing

SPEC = "MERISTEM-HARNESS-CYCLE-R061"


@pytest.mark.spec(SPEC)
async def test_turn_end_records_monotonic_elapsed_with_receipt_ref():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "log",
                    "params": {"op": "Evaluate"},
                    "answer": {"outcome": "committed"},
                }
            ]
        }
    )
    sent = await record_turn_timing(
        port,
        run_ref="r1",
        receipt_ref="rcpt-1",
        request_started_ns=2_000_000_000,
        run_started_ns=500_000_000,
        clock=lambda: 3_500_000_000,
    )
    assert sent is True
    op, body = port.bodies[0]
    assert op == "Evaluate" and body["receipt_ref"] == "rcpt-1"
    assert body["client_request_elapsed_ms"] == 1500
    assert body["client_run_elapsed_ms"] == 3000
    assert not {"tokens_per_second", "generation_ms"} & set(body)


@pytest.mark.spec(SPEC)
async def test_clock_failure_omits_the_observation():
    def broken():
        raise OSError("clock")

    port = RecordedEnginePort({"answers": []})
    assert not await record_turn_timing(
        port,
        run_ref="r",
        receipt_ref="x",
        request_started_ns=0,
        run_started_ns=0,
        clock=broken,
    )
    assert port.bodies == []
