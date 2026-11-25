"""Store the typed step graph before compile (MERISTEM-HARNESS-CYCLE-R022.1)."""

from __future__ import annotations

import hashlib

import ports_support as support
import pytest
from pydantic_ai.models.test import TestModel
from support.recorded_engine import RecordedEnginePort, params_digest

from meristem.graph import HarnessConfig, RunPlan, create_graph_agent, execute_graph
from meristem.harness import plan
from meristem.harness.run_lifecycle import RunLifecycle

DRAFT = {"steps": [{"type": "act"}, {"type": "verify"}]}
SOURCE_DIGEST = hashlib.sha256(plan.canonical_json(DRAFT).encode("utf-8")).hexdigest()


async def _run_with_consumer(
    hook, *, port, consumer_name="workflow_graph", consumers=("workflow_graph",)
) -> dict:
    """Drive one run whose ``on_end`` consumer is ``hook``."""
    lifecycle = RunLifecycle(consumers=consumers)
    lifecycle.add(consumer_name, "on_end", hook)
    plan_ = RunPlan(
        model=TestModel(custom_output_text="done"),
        steps=("q",),
        lifecycle=lifecycle,
        harness=HarnessConfig(consumers=consumers),
    )
    support.register_run_decision_provider(lambda request: plan_)
    if port is not None:
        support.register_engine_decision_port(port)
    graph, config = create_graph_agent(agent_model="m", ports=support.PORTS)
    return await execute_graph(graph, config, "q", run_id="r1", ports=support.PORTS)


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R022.1")
async def test_graph_stored_before_compile():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "put_blob",
                    "params_digest": params_digest({"sha256": SOURCE_DIGEST}),
                    "answer": {"outcome": "committed", "record_digest": "blob-1"},
                },
                {
                    "method": "log",
                    "params": {"op": "RunRecord"},
                    "answer": {"outcome": "committed", "record_digest": "record-1"},
                },
            ]
        }
    )
    support.register_engine_decision_port(port)

    stored: dict = {}

    async def store(_state) -> None:
        stored["result"] = await plan.store_then_compile(
            DRAFT, support.PORTS.engine_decision_port(), run_ref="r1"
        )

    result = await _run_with_consumer(store, port=port)

    assert result["status"] == "completed"
    # The store call precedes the compile call.
    assert [method for method, _ in port.calls] == ["put_blob", "log"]
    assert port.calls[0] == ("put_blob", params_digest({"sha256": SOURCE_DIGEST}))
    assert stored["result"].source_digest == SOURCE_DIGEST
    assert stored["result"].program_ref == "record-1"


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R022.1")
async def test_step_without_type_refuses_before_any_store():
    port = RecordedEnginePort({"answers": []})
    support.register_engine_decision_port(port)
    invalid = {"steps": [{"type": "act"}, {"prompt": "untitled"}]}

    async def store(_state) -> None:
        await plan.store_then_compile(
            invalid, support.PORTS.engine_decision_port(), run_ref="r1"
        )

    result = await _run_with_consumer(store, port=port)

    assert result["status"] == "failed"
    assert "workflow_graph_invalid" in (result["error"] or "")
    # Nothing was stored or compiled.
    assert port.calls == []


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R022.1")
async def test_unbound_engine_port_refuses_and_decides_nothing():
    # No engine port is bound for the run.

    async def store(_state) -> None:
        await plan.store_then_compile(
            DRAFT, support.PORTS.engine_decision_port(), run_ref="r1"
        )

    result = await _run_with_consumer(store, port=None)

    assert result["status"] == "failed"
    assert "engine_decision_unavailable" in (result["error"] or "")


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R022.1")
async def test_unlisted_consumer_never_runs():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "put_blob",
                    "params_digest": params_digest({"sha256": SOURCE_DIGEST}),
                    "answer": {"outcome": "committed"},
                }
            ]
        }
    )
    support.register_engine_decision_port(port)

    async def store(_state) -> None:
        await plan.store_then_compile(
            DRAFT, support.PORTS.engine_decision_port(), run_ref="r1"
        )

    result = await _run_with_consumer(
        store, port=port, consumers=(), consumer_name="other"
    )

    assert result["status"] == "completed"
    assert port.calls == []
