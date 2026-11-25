"""Apply the champion skill revision (MERISTEM-HARNESS-CYCLE-R043.1)."""

from __future__ import annotations

from typing import Any

import ports_support as support
import pytest
from pydantic_ai.models.test import TestModel
from support.recorded_engine import RecordedEnginePort, params_digest

from meristem.graph import HarnessConfig, RunPlan, create_graph_agent, execute_graph


async def _run(*, metadata: dict[str, Any], port) -> dict[str, Any]:
    plan = RunPlan(
        model=TestModel(custom_output_text="done"),
        steps=("q",),
        instructions="baseline instructions",
        harness=HarnessConfig(consumers=("champion_skill",)),
    )
    support.register_run_decision_provider(lambda request: plan)
    support.register_engine_decision_port(port)
    graph, config = create_graph_agent(agent_model="m", ports=support.PORTS)
    return await execute_graph(
        graph, config, "q", run_id="r1", metadata=metadata, ports=support.PORTS
    )


def _pointer_port(answer: dict[str, Any]) -> RecordedEnginePort:
    return RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "pointer",
                    "params": {"pointer_key": "champion:program:prog-1"},
                    "answer": answer,
                }
            ]
        }
    )


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R043.1")
async def test_champion_revision_applied(events) -> None:
    """One run admits the champion through the pointer the engine serves."""
    port = _pointer_port(
        {
            "outcome": "solved",
            "payload": {
                "revision_ref": "rev-7",
                "instructions": "champion instructions",
            },
        }
    )
    result = await _run(metadata={"harness_program_ref": "prog-1"}, port=port)

    assert result["status"] == "completed"
    # One pointer read under the program's champion key.
    assert port.calls == [
        ("pointer", params_digest({"pointer_key": "champion:program:prog-1"}))
    ]
    # The loaded revision equals the pointer's, and the pointer key and
    # the revision enter the run trace.
    assert events[0]["type"] == "champion_skill"
    assert events[0]["pointer_key"] == "champion:program:prog-1"
    assert events[0]["revision_ref"] == "rev-7"


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R043.1")
async def test_champion_refusals_fail_closed() -> None:
    # No pointer: the step refuses champion_missing and the run fails
    # closed.
    result = await _run(
        metadata={"harness_program_ref": "prog-1"},
        port=_pointer_port({"outcome": "solved", "payload": {}}),
    )
    assert result["status"] == "failed"
    assert "champion_missing" in (result["error"] or "")

    # Engine codes pass through unchanged.
    for code in ("PROMOTION_NO_BASELINE", "ROLLBACK_NO_PARENT"):
        result = await _run(
            metadata={"harness_program_ref": "prog-1"},
            port=_pointer_port({"outcome": "abstained", "refusal_code": code}),
        )
        assert result["status"] == "failed"
        assert code in (result["error"] or "")

    # With no bound port the step refuses engine_decision_unavailable
    # and decides nothing.
    result = await _run(metadata={"harness_program_ref": "prog-1"}, port=None)
    assert result["status"] == "failed"
    assert "engine_decision_unavailable" in (result["error"] or "")


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R043.1")
async def test_consumer_gate_decides_nothing_when_unlisted() -> None:
    port = RecordedEnginePort({"answers": []})
    plan = RunPlan(
        model=TestModel(custom_output_text="done"),
        steps=("q",),
        instructions="baseline instructions",
    )
    support.register_run_decision_provider(lambda request: plan)
    support.register_engine_decision_port(port)
    graph, config = create_graph_agent(agent_model="m", ports=support.PORTS)
    result = await execute_graph(
        graph,
        config,
        "q",
        run_id="r1",
        metadata={"harness_program_ref": "prog-1"},
        ports=support.PORTS,
    )

    assert result["status"] == "completed"
    assert port.calls == []
