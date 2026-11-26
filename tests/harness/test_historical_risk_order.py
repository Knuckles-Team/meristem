"""The change-planning step applies the received historical risk ordering."""

from __future__ import annotations

from support.recorded_engine import RecordedEnginePort

from meristem.harness.risk_order import (
    order_by_historical_risk,
    request_historical_risk,
)

RISK = {"ordering": ["c", "a", "b"], "unknown": ["z"]}


def test_step_order_follows_received_risk():
    result = order_by_historical_risk(["a", "b", "c", "z", "n"], RISK)
    assert result.ordered == ("c", "a", "b")
    assert result.review == ("z", "n")


async def test_request_reads_the_served_query():
    query = "PROPAGATE x RISK HISTORY"
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "UnifiedQuery",
                    "params": {"uql": query},
                    "answer": {"outcome": "solved", "payload": RISK},
                }
            ]
        }
    )
    result = await request_historical_risk(port, query, ["a", "c", "z"], run_ref="r1")
    assert result.ordered == ("c", "a") and result.review == ("z",)
