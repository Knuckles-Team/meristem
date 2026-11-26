"""Continue only verified steps (MERISTEM-HARNESS-CYCLE-R007.1)."""

from __future__ import annotations

import hashlib
import json

import ports_support as support
import pytest
from pydantic_ai.models.test import TestModel
from support.recorded_engine import RecordedEnginePort, params_digest

from meristem.graph import HarnessConfig, RunPlan, create_graph_agent, execute_graph
from meristem.harness import plan_runtime, run_lifecycle

SPEC = "MERISTEM-HARNESS-CYCLE-R007.1"
# spec: MERISTEM-HARNESS-CYCLE-R007.1

INPUT_DIGEST = hashlib.sha256(
    json.dumps([], sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )
).hexdigest()
OUTPUT_DIGEST = hashlib.sha256(b"done").hexdigest()


def _verification_params() -> dict:
    return {
        "step_id": "q",
        "input_digest": INPUT_DIGEST,
        "output_digest": OUTPUT_DIGEST,
        "evidence_ids": [],
    }


async def _run_after_step(hook, *, port: RecordedEnginePort | None) -> dict:
    lifecycle = run_lifecycle.RunLifecycle(consumers=("step_check",))
    lifecycle.add("step_check", "after_step", hook)
    plan = RunPlan(
        model=TestModel(custom_output_text="done"),
        steps=("q",),
        lifecycle=lifecycle,
        harness=HarnessConfig(consumers=("step_check",)),
    )
    support.register_run_decision_provider(lambda request: plan)
    if port is not None:
        support.register_engine_decision_port(port)
    graph, config = create_graph_agent(agent_model="m", ports=support.PORTS)
    return await execute_graph(graph, config, "q", run_id="r1", ports=support.PORTS)


@pytest.mark.spec(SPEC)
async def test_verified_step_continues():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "step_verification",
                    "params": _verification_params(),
                    "answer": {
                        "outcome": "solved",
                        "payload": {"verdict": "verified"},
                        "record_digest": "rec-1",
                    },
                }
            ]
        }
    )
    verdict: dict = {}

    async def check(state, step) -> None:
        verdict["result"] = await plan_runtime.verify_step(
            state, "q", support.PORTS.engine_decision_port(), run_ref="r1"
        )

    result = await _run_after_step(check, port=port)

    assert result["status"] == "completed"
    assert port.calls == [("step_verification", params_digest(_verification_params()))]
    assert verdict["result"].verdict == "verified"
    # The trace links to the record digest.
    assert verdict["result"].record_digest == "rec-1"


@pytest.mark.spec(SPEC)
async def test_unverified_step_not_continued():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "step_verification",
                    "params": _verification_params(),
                    "answer": {
                        "outcome": "solved",
                        "payload": {"verdict": "rejected"},
                    },
                }
            ]
        }
    )

    async def check(state, step) -> None:
        await plan_runtime.verify_step(
            state, "q", support.PORTS.engine_decision_port(), run_ref="r1"
        )

    result = await _run_after_step(check, port=port)

    assert result["status"] == "failed"
    assert "step_unverified" in (result["error"] or "")


@pytest.mark.spec(SPEC)
async def test_abstained_answer_not_verified():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "step_verification",
                    "params": _verification_params(),
                    "answer": {"outcome": "abstained"},
                }
            ]
        }
    )

    async def check(state, step) -> None:
        await plan_runtime.verify_step(
            state, "q", support.PORTS.engine_decision_port(), run_ref="r1"
        )

    result = await _run_after_step(check, port=port)

    assert result["status"] == "failed"
    assert "step_unverified" in (result["error"] or "")


@pytest.mark.spec(SPEC)
async def test_unbound_port_refuses_and_starts_nothing():
    async def check(state, step) -> None:
        await plan_runtime.verify_step(
            state, "q", support.PORTS.engine_decision_port(), run_ref="r1"
        )

    result = await _run_after_step(check, port=None)

    assert result["status"] == "failed"
    assert "engine_decision_unavailable" in (result["error"] or "")
