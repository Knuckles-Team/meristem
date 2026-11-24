"""Replace the active context only on a sealed replace result
(MERISTEM-HARNESS-CYCLE-R017.4)."""

from __future__ import annotations

import ports_support as support
import pytest
from support.recorded_engine import RecordedEnginePort, params_digest

from meristem.harness import plan_runtime
from meristem.harness.run_lifecycle import ConsumerRefused

SPEC = "MERISTEM-HARNESS-CYCLE-R017.4"
# spec: MERISTEM-HARNESS-CYCLE-R017.4

RAW = {"q1": "pass", "q2": "pass"}
CANDIDATE = {"q1": "pass", "q2": "fail"}
QUERIES = ("q1", "q2")
PAIRED = {
    "q1": {"raw": RAW["q1"], "candidate": CANDIDATE["q1"]},
    "q2": {"raw": RAW["q2"], "candidate": CANDIDATE["q2"]},
}


@pytest.mark.spec(SPEC)
async def test_replace_and_retain_cases():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "context_replacement",
                    "params": {"paired": PAIRED},
                    "answer": {
                        "outcome": "solved",
                        "payload": {"result": "replace"},
                        "record_digest": "rec-1",
                    },
                },
                {
                    "method": "context_replacement",
                    "params": {"paired": PAIRED},
                    "answer": {
                        "outcome": "solved",
                        "payload": {"result": "retain"},
                    },
                },
            ]
        }
    )
    support.register_engine_decision_port(port)

    replaced = await plan_runtime.consider_context_replacement(
        RAW, CANDIDATE, QUERIES, support.PORTS.engine_decision_port(), run_ref="r1"
    )
    kept = await plan_runtime.consider_context_replacement(
        RAW, CANDIDATE, QUERIES, support.PORTS.engine_decision_port(), run_ref="r1"
    )

    assert port.calls == [
        ("context_replacement", params_digest({"paired": PAIRED})),
        ("context_replacement", params_digest({"paired": PAIRED})),
    ]
    # Only the sealed replace result replaces the active context.
    assert replaced.replaced is True
    assert replaced.result == "replace"
    assert replaced.record_digest == "rec-1"
    assert kept.replaced is False
    assert kept.result == "retain"
    assert replaced.verdict_digest == kept.verdict_digest


@pytest.mark.spec(SPEC)
async def test_query_missing_one_side_refuses():
    port = RecordedEnginePort({"answers": []})
    support.register_engine_decision_port(port)

    with pytest.raises(ConsumerRefused, match="paired_set_incomplete"):
        await plan_runtime.consider_context_replacement(
            {"q1": "pass"},
            CANDIDATE,
            QUERIES,
            support.PORTS.engine_decision_port(),
            run_ref="r1",
        )

    # The refusal happens before any engine question.
    assert port.calls == []
