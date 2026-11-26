"""Send the subgoal hierarchy and apply the curriculum verdict
(MERISTEM-HARNESS-CYCLE-R028.1)."""

from __future__ import annotations

import ports_support as support
import pytest
from pydantic_ai.models.test import TestModel
from support.recorded_engine import RecordedEnginePort, params_digest

from meristem.graph import HarnessConfig, RunPlan, create_graph_agent, execute_graph
from meristem.harness import plan_runtime, run_lifecycle

SPEC = "MERISTEM-HARNESS-CYCLE-R028.1"
# spec: MERISTEM-HARNESS-CYCLE-R028.1

HIERARCHY = [
    {"id": "g1", "children": [{"id": "g2", "children": []}]},
]
REWARD_PARAMS = {
    "hierarchy": HIERARCHY,
    "checkpoints": [{"id": "cp-1", "met": True}],
    "trajectory": [{"step": 1, "score": 0.5}],
}
REWARDS = {"g1": 0.5, "g2": 0.9}
CURRICULUM = {
    "hierarchy": HIERARCHY,
    "depth": 2,
    "checkpoints": [{"id": "cp-1", "met": True}],
    "trajectory": [{"step": 1, "score": 0.5}],
}


async def _run_before_step(hook, *, port: RecordedEnginePort | None) -> dict:
    lifecycle = run_lifecycle.RunLifecycle(consumers=("curriculum",))
    lifecycle.add("curriculum", "before_step", hook)
    plan = RunPlan(
        model=TestModel(custom_output_text="done"),
        steps=("q",),
        lifecycle=lifecycle,
        harness=HarnessConfig(consumers=("curriculum",)),
    )
    support.register_run_decision_provider(lambda request: plan)
    if port is not None:
        support.register_engine_decision_port(port)
    graph, config = create_graph_agent(agent_model="m", ports=support.PORTS)
    return await execute_graph(graph, config, "q", run_id="r1", ports=support.PORTS)


@pytest.mark.spec(SPEC)
async def test_hierarchy_sent_and_verdict_applied():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "reward_decomposition",
                    "params": REWARD_PARAMS,
                    "answer": {
                        "outcome": "solved",
                        "payload": REWARDS,
                        "record_digest": "rec-1",
                    },
                },
                {
                    "method": "curriculum_promotion",
                    "params": {"rewards": REWARDS},
                    "answer": {
                        "outcome": "solved",
                        "payload": {"verdict": "promote"},
                        "record_digest": "rec-2",
                    },
                },
            ]
        }
    )
    decision: dict = {}

    async def submit(state) -> None:
        state.curriculum = CURRICULUM
        decision["result"] = await plan_runtime.submit_curriculum(
            state, support.PORTS.engine_decision_port(), run_ref="r1"
        )

    result = await _run_before_step(submit, port=port)

    assert result["status"] == "completed"
    assert port.calls == [
        ("reward_decomposition", params_digest(REWARD_PARAMS)),
        ("curriculum_promotion", params_digest({"rewards": REWARDS})),
    ]
    # The step rewards come from the answer; the verdict applies to the next horizon.
    assert decision["result"].rewards == REWARDS
    assert decision["result"].verdict == "promote"
    assert decision["result"].promoted is True
    assert (
        decision["result"].record_digest,
        decision["result"].promotion_digest,
    ) == ("rec-1", "rec-2")


@pytest.mark.spec(SPEC)
async def test_hold_verdict_not_promoted():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "reward_decomposition",
                    "params": REWARD_PARAMS,
                    "answer": {"outcome": "solved", "payload": REWARDS},
                },
                {
                    "method": "curriculum_promotion",
                    "params": {"rewards": REWARDS},
                    "answer": {
                        "outcome": "solved",
                        "payload": {"verdict": "hold"},
                    },
                },
            ]
        }
    )
    decision: dict = {}

    async def submit(state) -> None:
        state.curriculum = CURRICULUM
        decision["result"] = await plan_runtime.submit_curriculum(
            state, support.PORTS.engine_decision_port(), run_ref="r1"
        )

    result = await _run_before_step(submit, port=port)

    assert result["status"] == "completed"
    assert decision["result"].verdict == "hold"
    assert decision["result"].promoted is False


@pytest.mark.spec(SPEC)
async def test_hierarchy_deeper_than_received_depth_refuses():
    port = RecordedEnginePort({"answers": []})

    async def submit(state) -> None:
        state.curriculum = {
            "hierarchy": HIERARCHY,
            "depth": 1,
            "checkpoints": [],
            "trajectory": [],
        }
        await plan_runtime.submit_curriculum(
            state, support.PORTS.engine_decision_port(), run_ref="r1"
        )

    result = await _run_before_step(submit, port=port)

    assert result["status"] == "failed"
    assert "hierarchy_too_deep" in (result["error"] or "")
    # The refusal happens before any engine question.
    assert port.calls == []
