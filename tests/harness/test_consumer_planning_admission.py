"""Ask planning admission after study (MERISTEM-HARNESS-CYCLE-R026.1)."""

from __future__ import annotations

import ports_support as support
import pytest
from pydantic_ai.models.test import TestModel
from support.recorded_engine import RecordedEnginePort, params_digest

from meristem.graph import HarnessConfig, RunPlan, create_graph_agent, execute_graph
from meristem.harness import plan as harness_plan
from meristem.harness import run_lifecycle

SPEC = "MERISTEM-HARNESS-CYCLE-R026.1"
# spec: MERISTEM-HARNESS-CYCLE-R026.1

PROPOSALS = ("gap-1", "gap-2")
PARAMS = {"repo": "repo", "revision": "rev-9", "proposals": ["gap-1", "gap-2"]}


async def _run_before_step(hook, *, port: RecordedEnginePort | None) -> dict:
    """One run whose admitted ``before_step`` consumer is ``hook``."""
    lifecycle = run_lifecycle.RunLifecycle(consumers=("planning_admission",))
    lifecycle.add("planning_admission", "before_step", hook)
    plan = RunPlan(
        model=TestModel(custom_output_text="done"),
        steps=("q",),
        lifecycle=lifecycle,
        harness=HarnessConfig(consumers=("planning_admission",)),
    )
    support.register_run_decision_provider(lambda request: plan)
    if port is not None:
        support.register_engine_decision_port(port)
    graph, config = create_graph_agent(agent_model="m", ports=support.PORTS)
    return await execute_graph(graph, config, "q", run_id="r1", ports=support.PORTS)


@pytest.mark.spec(SPEC)
async def test_gap_proposals_sent_before_planning():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "planning_admission",
                    "params": PARAMS,
                    "answer": {
                        "outcome": "solved",
                        "payload": {"admitted": ["gap-1"]},
                        "record_digest": "adm-1",
                    },
                }
            ]
        }
    )
    admitted: dict = {}

    async def ask(_state) -> None:
        admitted["result"] = await harness_plan.ask_planning_admission(
            PROPOSALS,
            "repo",
            "rev-9",
            support.PORTS.engine_decision_port(),
            run_ref="r1",
        )

    result = await _run_before_step(ask, port=port)

    assert result["status"] == "completed"
    assert port.calls == [("planning_admission", params_digest(PARAMS))]
    outcome = admitted["result"]
    # Only the admitted proposals are planned.
    assert outcome.admitted == ("gap-1",)
    assert "gap-2" not in outcome.admitted
    assert outcome.record_digest == "adm-1"


@pytest.mark.spec(SPEC)
async def test_answer_that_admits_nothing_refuses_planning_not_admitted():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "planning_admission",
                    "params": PARAMS,
                    "answer": {
                        "outcome": "solved",
                        "payload": {"admitted": []},
                    },
                }
            ]
        }
    )

    async def ask(_state) -> None:
        await harness_plan.ask_planning_admission(
            PROPOSALS,
            "repo",
            "rev-9",
            support.PORTS.engine_decision_port(),
            run_ref="r1",
        )

    result = await _run_before_step(ask, port=port)

    assert result["status"] == "failed"
    assert "planning_not_admitted" in (result["error"] or "")


@pytest.mark.spec(SPEC)
async def test_abstained_answer_plans_nothing():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "planning_admission",
                    "params": PARAMS,
                    "answer": {"outcome": "abstained"},
                }
            ]
        }
    )

    async def ask(_state) -> None:
        await harness_plan.ask_planning_admission(
            PROPOSALS,
            "repo",
            "rev-9",
            support.PORTS.engine_decision_port(),
            run_ref="r1",
        )

    result = await _run_before_step(ask, port=port)

    assert result["status"] == "failed"
    assert "planning_not_admitted" in (result["error"] or "")


@pytest.mark.spec(SPEC)
async def test_admitted_id_outside_the_question_is_malformed():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "planning_admission",
                    "params": PARAMS,
                    "answer": {
                        "outcome": "solved",
                        "payload": {"admitted": ["gap-3"]},
                    },
                }
            ]
        }
    )

    async def ask(_state) -> None:
        await harness_plan.ask_planning_admission(
            PROPOSALS,
            "repo",
            "rev-9",
            support.PORTS.engine_decision_port(),
            run_ref="r1",
        )

    result = await _run_before_step(ask, port=port)

    assert result["status"] == "failed"
    assert "outside the question" in (result["error"] or "")


@pytest.mark.spec(SPEC)
async def test_unbound_port_refuses_and_the_guarded_action_does_not_start():
    async def ask(_state) -> None:
        await harness_plan.ask_planning_admission(
            PROPOSALS,
            "repo",
            "rev-9",
            support.PORTS.engine_decision_port(),
            run_ref="r1",
        )

    result = await _run_before_step(ask, port=None)

    assert result["status"] == "failed"
    assert "engine_decision_unavailable" in (result["error"] or "")
