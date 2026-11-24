"""Run the retrieval fallback window exactly once
(MERISTEM-HARNESS-CYCLE-R032.1)."""

from __future__ import annotations

import ports_support as support
import pytest
from support.recorded_engine import RecordedEnginePort, params_digest

from meristem.harness import plan_runtime
from meristem.harness.run_lifecycle import ConsumerRefused

SPEC = "MERISTEM-HARNESS-CYCLE-R032.1"
# spec: MERISTEM-HARNESS-CYCLE-R032.1

QUESTION = {"text": "when did the release ship"}
WINDOW = {"start": "2026-01-01", "end": "2026-01-03"}
COVERAGE = {"hits": 2}


@pytest.mark.spec(SPEC)
async def test_fallback_window_runs_once():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "temporal_basis",
                    "params": {"question": QUESTION},
                    "answer": {
                        "outcome": "solved",
                        "payload": {"window": WINDOW},
                        "record_digest": "basis-1",
                    },
                },
                {
                    "method": "UnifiedQuery",
                    "params": {"question": QUESTION, "window": WINDOW},
                    "answer": {
                        "outcome": "solved",
                        "payload": COVERAGE,
                        "record_digest": "cov-1",
                    },
                },
            ]
        }
    )
    support.register_engine_decision_port(port)

    window = await plan_runtime.run_fallback_window(
        QUESTION, support.PORTS.engine_decision_port(), run_ref="r1"
    )

    # The basis decision, then exactly one UnifiedQuery run: never widened locally.
    assert port.calls == [
        ("temporal_basis", params_digest({"question": QUESTION})),
        ("UnifiedQuery", params_digest({"question": QUESTION, "window": WINDOW})),
    ]
    assert window is not None
    assert window.window == WINDOW
    assert window.coverage == COVERAGE
    assert window.coverage_digest == "cov-1"


@pytest.mark.spec(SPEC)
async def test_second_run_refuses_fallback_window_repeated():
    port = RecordedEnginePort({"answers": []})
    support.register_engine_decision_port(port)

    with pytest.raises(ConsumerRefused, match="fallback_window_repeated"):
        await plan_runtime.run_fallback_window(
            QUESTION,
            support.PORTS.engine_decision_port(),
            run_ref="r1",
            already_ran=True,
        )

    assert port.calls == []


@pytest.mark.spec(SPEC)
async def test_abstained_basis_returns_none():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "temporal_basis",
                    "params": {"question": QUESTION},
                    "answer": {"outcome": "abstained"},
                }
            ]
        }
    )
    support.register_engine_decision_port(port)

    window = await plan_runtime.run_fallback_window(
        QUESTION, support.PORTS.engine_decision_port(), run_ref="r1"
    )

    assert window is None
    assert len(port.calls) == 1
