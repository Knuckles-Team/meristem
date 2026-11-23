"""Record and send the run observation window (MERISTEM-HARNESS-CYCLE-R071)."""

from __future__ import annotations

import pytest
from pydantic_ai.usage import RunUsage
from support.recorded_engine import RecordedEnginePort, params_digest

from meristem.graph import budget
from meristem.graph.state import RunBudget
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineDecisionRefused

SPEC = "MERISTEM-HARNESS-CYCLE-R071"

BUDGET = RunBudget(
    request_limit=3,
    tool_calls_limit=None,
    total_tokens_limit=100,
    timeout_seconds=60.0,
    max_concurrent_requests=2,
)
USAGE = RunUsage(requests=1, tool_calls=0, input_tokens=10, output_tokens=5)

PAYLOAD = {
    "run_id": "r1",
    "request_limit": 3,
    "tool_calls_limit": None,
    "total_tokens_limit": 100,
    "max_concurrent_requests": 2,
    "timeout_seconds": 60.0,
    "requests": 1,
    "tool_calls": 0,
    "input_tokens": 10,
    "output_tokens": 5,
}


def _port(option_ids: tuple[str, ...]) -> RecordedEnginePort:
    return RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "run_adjustment",
                    "params": PAYLOAD,
                    "answer": {
                        "outcome": "solved",
                        "option_ids": option_ids,
                    },
                }
            ]
        }
    )


@pytest.mark.spec(SPEC)
async def test_window_sent_and_widening_refused():
    # The window is sent once per run with unknown values as unknown, and a
    # received narrower width applies at once.
    narrow = _port(("width:1",))
    updated = await budget.send_observation_window("r1", BUDGET, USAGE, narrow)

    assert narrow.calls == [("run_adjustment", params_digest(PAYLOAD))]
    assert updated.max_concurrent_requests == 1
    assert updated is not BUDGET

    # A wider width mid-run is refused.
    wide = _port(("width:3",))
    with pytest.raises(ConsumerRefused, match="width_widening_refused"):
        await budget.send_observation_window("r1", BUDGET, USAGE, wide)


@pytest.mark.spec(SPEC)
async def test_unbound_port_refuses():
    with pytest.raises(EngineDecisionRefused, match="engine_decision_unavailable"):
        await budget.send_observation_window("r1", BUDGET, USAGE, None)


@pytest.mark.spec(SPEC)
async def test_abstained_answer_changes_nothing():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "run_adjustment",
                    "params": PAYLOAD,
                    "answer": {"outcome": "abstained"},
                }
            ]
        }
    )

    updated = await budget.send_observation_window("r1", BUDGET, USAGE, port)

    assert updated is BUDGET
