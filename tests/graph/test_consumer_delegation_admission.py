"""Delegate only against an admitted verdict (MERISTEM-HARNESS-CYCLE-R021.9)."""

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

SPEC = "MERISTEM-HARNESS-CYCLE-R021.9"

TASK_CLASS = "code"


def _params() -> dict:
    return {
        "task_class": TASK_CLASS,
        "metric_id": None,
        "threshold_q32": None,
        "window_start_ms": None,
        "window_end_ms": None,
    }


DIGEST = params_digest(_params())


def _port(answer: dict) -> RecordedEnginePort:
    return RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "delegation_admission",
                    "params": _params(),
                    "answer": answer,
                }
            ]
        }
    )


async def _run_before_step(
    hook, *, port: RecordedEnginePort | None
) -> tuple[dict, dict]:
    """One run whose admitted ``before_step`` consumer is ``hook``."""
    lifecycle = run_lifecycle.RunLifecycle(consumers=("delegation",))
    lifecycle.add("delegation", "before_step", hook)
    plan = RunPlan(
        model=TestModel(custom_output_text="done"),
        steps=("q",),
        lifecycle=lifecycle,
        harness=HarnessConfig(consumers=("delegation",)),
    )
    support.register_run_decision_provider(lambda request: plan)
    if port is not None:
        support.register_engine_decision_port(port)
    graph, config = create_graph_agent(agent_model="m", ports=support.PORTS)
    result = await execute_graph(graph, config, "q", run_id="r1", ports=support.PORTS)
    return result, {}


@pytest.mark.spec(SPEC)
async def test_stale_or_refused_verdict_blocks_delegation():
    admitted: dict = {}

    async def delegate(state) -> None:
        admitted["admission"] = await budget.require_delegation_admission(
            TASK_CLASS, support.PORTS.engine_decision_port(), run_ref="r1"
        )

    # A refused verdict blocks the delegation.
    refused = _port(
        {
            "outcome": "solved",
            "payload": {"verdict": "refuse"},
            "record_digest": "ver-1",
        }
    )
    result, _ = await _run_before_step(delegate, port=refused)
    assert result["status"] == "failed"
    assert "delegation_not_admitted" in (result["error"] or "")
    assert "admission" not in admitted
    # The verdict is asked fresh: one call, no cached retry.
    assert refused.calls == [("delegation_admission", DIGEST)]

    # An abstained verdict is stale too: it admits nothing.
    stale = _port({"outcome": "abstained"})
    result, _ = await _run_before_step(delegate, port=stale)
    assert result["status"] == "failed"
    assert "delegation_not_admitted" in (result["error"] or "")
    assert "admission" not in admitted

    # A fresh admit verdict starts the delegation with its record digest.
    admit = _port(
        {"outcome": "solved", "payload": {"verdict": "admit"}, "record_digest": "ver-2"}
    )
    result, _ = await _run_before_step(delegate, port=admit)
    assert result["status"] == "completed"
    admission = admitted["admission"]
    assert (admission.task_class, admission.record_digest) == (TASK_CLASS, "ver-2")


@pytest.mark.spec(SPEC)
async def test_engine_refusal_passes_through():
    port = _port({"outcome": "abstained", "refusal_code": "DELEGATION_DENIED"})

    async def delegate(state) -> None:
        await budget.require_delegation_admission(
            TASK_CLASS, support.PORTS.engine_decision_port(), run_ref="r1"
        )

    result, _ = await _run_before_step(delegate, port=port)

    assert result["status"] == "failed"
    assert "DELEGATION_DENIED" in (result["error"] or "")


@pytest.mark.spec(SPEC)
async def test_unbound_port_refuses_and_the_delegation_does_not_start():
    admitted: dict = {}

    async def delegate(state) -> None:
        admitted["admission"] = await budget.require_delegation_admission(
            TASK_CLASS, support.PORTS.engine_decision_port(), run_ref="r1"
        )

    result, _ = await _run_before_step(delegate, port=None)

    assert result["status"] == "failed"
    assert "engine_decision_unavailable" in (result["error"] or "")
    assert "admission" not in admitted
