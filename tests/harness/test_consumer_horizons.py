"""Run the plan horizon by horizon and checkpoint each one
(MERISTEM-HARNESS-CYCLE-R021.3)."""

from __future__ import annotations

import ports_support as support
import pytest
from support.recorded_engine import RecordedEnginePort, params_digest

from meristem.harness import plan_runtime
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineDecisionRefused

SPEC = "MERISTEM-HARNESS-CYCLE-R021.3"
# spec: MERISTEM-HARNESS-CYCLE-R021.3

PARTITION = [
    {"consumed": 10, "reserved": 5, "ceiling": 100, "rule": "h1"},
    {"consumed": 0, "reserved": 0, "ceiling": 50, "rule": "h2"},
]


@pytest.mark.spec(SPEC)
async def test_horizons_run_in_order_with_checkpoints():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "log",
                    "params": {"op": "checkpoint"},
                    "answer": {"outcome": "solved", "record_digest": "chk-1"},
                },
                {
                    "method": "log",
                    "params": {"op": "checkpoint"},
                    "answer": {"outcome": "solved", "record_digest": "chk-2"},
                },
            ]
        }
    )
    support.register_engine_decision_port(port)

    checkpoints = await plan_runtime.run_horizons(
        "plan-digest", PARTITION, support.PORTS.engine_decision_port(), run_ref="r1"
    )

    # One checkpoint write per horizon, in horizon order.
    assert port.calls == [
        ("log", params_digest({"op": "checkpoint"})),
        ("log", params_digest({"op": "checkpoint"})),
    ]
    first, second = checkpoints
    assert (first.horizon_index, second.horizon_index) == (0, 1)
    assert first.remaining == 100 - 10 - 5
    assert second.remaining == 50
    assert (first.rule, second.rule) == ("h1", "h2")
    assert (first.record_digest, second.record_digest) == ("chk-1", "chk-2")
    assert first.plan_digest == "plan-digest"


@pytest.mark.spec(SPEC)
async def test_consumed_plus_reserved_past_ceiling_refuses():
    port = RecordedEnginePort({"answers": []})
    support.register_engine_decision_port(port)

    with pytest.raises(ConsumerRefused, match="checkpoint_overflow"):
        await plan_runtime.run_horizons(
            "plan-digest",
            [{"consumed": 60, "reserved": 50, "ceiling": 100, "rule": "h1"}],
            support.PORTS.engine_decision_port(),
            run_ref="r1",
        )

    assert port.calls == []


@pytest.mark.spec(SPEC)
async def test_unbound_port_refuses_and_decides_nothing():
    with pytest.raises(EngineDecisionRefused, match="engine_decision_unavailable"):
        await plan_runtime.run_horizons(
            "plan-digest", PARTITION, support.PORTS.engine_decision_port(), run_ref="r1"
        )
