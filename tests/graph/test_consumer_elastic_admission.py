"""Re-admit width and depth changes (MERISTEM-HARNESS-CYCLE-R010.1)."""

from __future__ import annotations

import ports_support as support
import pytest
from pydantic_ai.models.test import TestModel
from support.recorded_engine import RecordedEnginePort, params_digest

from meristem.graph import (
    HarnessConfig,
    RunPlan,
    budget,
    create_graph_agent,
    execute_graph,
)
from meristem.harness import run_lifecycle

SPEC = "MERISTEM-HARNESS-CYCLE-R010.1"
# spec: MERISTEM-HARNESS-CYCLE-R010.1


def _question(width: int, depth: int) -> str:
    """The params digest of one new topology question for the wanted width."""
    return params_digest(
        {"kind": "topology_admission", "run_id": "r1", "width": width, "depth": depth}
    )


async def _run_before_step(
    hook, *, port: RecordedEnginePort | None
) -> tuple[dict, dict]:
    """One run whose admitted ``before_step`` consumer is ``hook``."""
    lifecycle = run_lifecycle.RunLifecycle(consumers=("elastic_admission",))
    lifecycle.add("elastic_admission", "before_step", hook)
    plan = RunPlan(
        model=TestModel(custom_output_text="done"),
        steps=("q",),
        lifecycle=lifecycle,
        harness=HarnessConfig(consumers=("elastic_admission",)),
    )
    support.register_run_decision_provider(lambda request: plan)
    if port is not None:
        support.register_engine_decision_port(port)
    graph, config = create_graph_agent(agent_model="m", ports=support.PORTS)
    result = await execute_graph(graph, config, "q", run_id="r1", ports=support.PORTS)
    return result, {}


@pytest.mark.spec(SPEC)
async def test_wider_run_asks_new_topology_question():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "assemble",
                    "params_digest": _question(4, 1),
                    "answer": {
                        "outcome": "solved",
                        "payload": {"max_width": 2, "max_depth": 1, "max_rounds": 3},
                        "record_digest": "en-1",
                    },
                }
            ]
        }
    )
    admitted: dict = {}

    async def admit(state) -> None:
        admitted["topology"] = await budget.request_wider_admission(
            state, 4, 1, support.PORTS.engine_decision_port()
        )

    result, _ = await _run_before_step(admit, port=port)

    assert result["status"] == "completed"
    # One new topology question waits for the answer before any widening.
    assert port.calls == [("assemble", _question(4, 1))]
    topology = admitted["topology"]
    # The run widens only to the admitted EnforcementRecord caps.
    assert (topology.width, topology.depth, topology.rounds) == (2, 1, 3)
    assert topology.record_digest == "en-1"


@pytest.mark.spec(SPEC)
async def test_engine_refusal_passes_through_and_keeps_admitted_width():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "assemble",
                    "params_digest": _question(4, 1),
                    "answer": {
                        "outcome": "abstained",
                        "refusal_code": "TOPOLOGY_WIDTH_EXCEEDS_CAP",
                    },
                }
            ]
        }
    )
    admitted: dict = {}

    async def admit(state) -> None:
        admitted["topology"] = await budget.request_wider_admission(
            state, 4, 1, support.PORTS.engine_decision_port()
        )

    result, _ = await _run_before_step(admit, port=port)

    assert result["status"] == "failed"
    assert "TOPOLOGY_WIDTH_EXCEEDS_CAP" in (result["error"] or "")
    # Never widened on a refusal: the guarded action did not start.
    assert "topology" not in admitted


@pytest.mark.spec(SPEC)
async def test_answer_without_admitted_caps_refuses_admission_unanswered():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "assemble",
                    "params_digest": _question(4, 1),
                    "answer": {"outcome": "solved", "payload": {}},
                }
            ]
        }
    )

    async def admit(state) -> None:
        await budget.request_wider_admission(
            state, 4, 1, support.PORTS.engine_decision_port()
        )

    result, _ = await _run_before_step(admit, port=port)

    assert result["status"] == "failed"
    assert "admission_unanswered" in (result["error"] or "")


@pytest.mark.spec(SPEC)
async def test_unbound_port_refuses_and_the_guarded_action_does_not_start():
    admitted: dict = {}

    async def admit(state) -> None:
        admitted["topology"] = await budget.request_wider_admission(
            state, 4, 1, support.PORTS.engine_decision_port()
        )

    result, _ = await _run_before_step(admit, port=None)

    assert result["status"] == "failed"
    assert "engine_decision_unavailable" in (result["error"] or "")
    assert "topology" not in admitted
