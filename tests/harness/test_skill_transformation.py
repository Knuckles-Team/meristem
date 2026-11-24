"""Apply a received skill transformation (MERISTEM-HARNESS-CYCLE-R059)."""

from __future__ import annotations

import ports_support as support
import pytest
from pydantic_ai.models.test import TestModel

from meristem.graph import HarnessConfig, RunPlan, create_graph_agent, execute_graph
from meristem.harness import plan as harness_plan
from meristem.harness import run_lifecycle

SPEC = "MERISTEM-HARNESS-CYCLE-R059"
# spec: MERISTEM-HARNESS-CYCLE-R059


async def _run_on_start(hook, *, plan: RunPlan) -> tuple[dict, RunPlan]:
    """One run whose admitted ``on_start`` consumer is ``hook``."""
    plan.lifecycle.add("skill_transformation", "on_start", hook)
    support.register_run_decision_provider(lambda request: plan)
    graph, config = create_graph_agent(agent_model="m", ports=support.PORTS)
    result = await execute_graph(graph, config, "q", run_id="r1", ports=support.PORTS)
    return result, plan


def _plan(transformation: dict | None) -> RunPlan:
    lifecycle = run_lifecycle.RunLifecycle(consumers=("skill_transformation",))
    return RunPlan(
        model=TestModel(custom_output_text="done"),
        steps=("q",),
        skill_transformation=transformation,
        lifecycle=lifecycle,
        harness=HarnessConfig(consumers=("skill_transformation",)),
    )


@pytest.mark.spec(SPEC)
async def test_received_transformation_applied():
    transformation = {
        "instructions": "use the revised skill",
        "steps": ["step-one"],
        "model": "m-9",
    }
    applied: dict = {}

    async def apply(_request, plan) -> None:
        applied["plan"] = harness_plan.apply_skill_transformation(
            plan, plan.skill_transformation
        )

    result, plan = await _run_on_start(apply, plan=_plan(transformation))

    assert result["status"] == "completed"
    updated = applied["plan"]
    # The received values replace the plan's values as received; the
    # harness ranks nothing.
    assert updated.instructions == "use the revised skill"
    assert updated.steps == ("step-one",)
    assert updated.model == "m-9"
    # The received plan is untouched.
    assert (plan.instructions, plan.steps) == ("", ("q",))


@pytest.mark.spec(SPEC)
async def test_invalid_received_value_refuses():
    applied: dict = {}

    async def apply(_request, plan) -> None:
        applied["plan"] = harness_plan.apply_skill_transformation(
            plan, plan.skill_transformation
        )

    result, _ = await _run_on_start(apply, plan=_plan({"model": 7}))

    assert result["status"] == "failed"
    assert "SKILL_TRANSFORMATION_INVALID" in (result["error"] or "")
    assert "plan" not in applied


@pytest.mark.spec(SPEC)
async def test_key_the_plan_does_not_carry_refuses():
    async def apply(_request, plan) -> None:
        harness_plan.apply_skill_transformation(plan, plan.skill_transformation)

    result, _ = await _run_on_start(apply, plan=_plan({"weight": 3}))

    assert result["status"] == "failed"
    assert "SKILL_TRANSFORMATION_INVALID" in (result["error"] or "")


@pytest.mark.spec(SPEC)
async def test_absent_transformation_changes_nothing():
    applied: dict = {}

    async def apply(_request, plan) -> None:
        applied["plan"] = harness_plan.apply_skill_transformation(
            plan, plan.skill_transformation
        )

    result, plan = await _run_on_start(apply, plan=_plan(None))

    assert result["status"] == "completed"
    assert applied["plan"] is plan
