"""Resolve each workflow through the registry before execution
(MERISTEM-RUN-R005.1)."""

from __future__ import annotations

import ports_support as support
import pytest
from pydantic_ai.models.test import TestModel

from meristem.graph import HarnessConfig, RunPlan, create_graph_agent, execute_graph
from meristem.harness import plan as harness_plan
from meristem.harness import run_lifecycle

SPEC = "MERISTEM-RUN-R005.1"
# spec: MERISTEM-RUN-R005.1


class FakeRegistry:
    """A host-bound workflow registry reader for the test."""

    def __init__(self, entries: dict) -> None:
        self._entries = entries
        self.calls: list[tuple[str, str]] = []

    async def read(self, name: str, *, run_ref: str):
        self.calls.append((name, run_ref))
        return self._entries.get(name)


async def _run_before_step(hook) -> dict:
    """One run whose admitted ``before_step`` consumer is ``hook``."""
    lifecycle = run_lifecycle.RunLifecycle(consumers=("workflows",))
    lifecycle.add("workflows", "before_step", hook)
    plan = RunPlan(
        model=TestModel(custom_output_text="done"),
        steps=("q",),
        lifecycle=lifecycle,
        harness=HarnessConfig(consumers=("workflows",)),
    )
    support.register_run_decision_provider(lambda request: plan)
    graph, config = create_graph_agent(agent_model="m", ports=support.PORTS)
    return await execute_graph(graph, config, "q", run_id="r1", ports=support.PORTS)


@pytest.mark.spec(SPEC)
async def test_workflow_resolved_before_execution():
    registry = FakeRegistry(
        {"wf-1": harness_plan.WorkflowEntry(id="wf-1", version="3", digest="d-1")}
    )
    resolved: dict = {}

    async def resolve(_state) -> None:
        resolved["entries"] = await harness_plan.resolve_workflows(
            ("wf-1",),
            registry,
            expected_digests={"wf-1": "d-1"},
            run_ref="r1",
        )

    result = await _run_before_step(resolve)

    # The registry is read once per name, before the step runs.
    assert registry.calls == [("wf-1", "r1")]
    assert result["status"] == "completed"
    entry = resolved["entries"][0]
    assert (entry.id, entry.version, entry.digest) == ("wf-1", "3", "d-1")


@pytest.mark.spec(SPEC)
async def test_unknown_workflow_refuses_and_no_step_runs():
    registry = FakeRegistry({})
    resolved: dict = {}

    async def resolve(_state) -> None:
        resolved["entries"] = await harness_plan.resolve_workflows(
            ("wf-1",), registry, run_ref="r1"
        )

    result = await _run_before_step(resolve)

    assert result["status"] == "failed"
    assert "WORKFLOW_UNKNOWN" in (result["error"] or "")
    assert "entries" not in resolved


@pytest.mark.spec(SPEC)
async def test_digest_differing_from_the_plan_refuses():
    registry = FakeRegistry(
        {"wf-1": harness_plan.WorkflowEntry(id="wf-1", version="4", digest="d-2")}
    )

    async def resolve(_state) -> None:
        await harness_plan.resolve_workflows(
            ("wf-1",), registry, expected_digests={"wf-1": "d-1"}, run_ref="r1"
        )

    result = await _run_before_step(resolve)

    assert result["status"] == "failed"
    assert "WORKFLOW_DIGEST_MISMATCH" in (result["error"] or "")


@pytest.mark.spec(SPEC)
async def test_unbound_registry_refuses_and_no_step_runs():
    resolved: dict = {}

    async def resolve(_state) -> None:
        resolved["entries"] = await harness_plan.resolve_workflows(
            ("wf-1",), None, run_ref="r1"
        )

    result = await _run_before_step(resolve)

    assert result["status"] == "failed"
    assert "WORKFLOW_REGISTRY_UNAVAILABLE" in (result["error"] or "")
    assert "entries" not in resolved
