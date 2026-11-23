"""Run a comparative-analysis plan within stage budgets
(MERISTEM-HARNESS-CYCLE-R023.1)."""

from __future__ import annotations

import pytest

from meristem.graph import budget
from meristem.graph.state import RunBudget, RunPlan
from meristem.harness.run_lifecycle import ConsumerRefused

SPEC = "MERISTEM-HARNESS-CYCLE-R023.1"


def _plan(sources, request_limit: int | None = None) -> RunPlan:
    return RunPlan(
        model="m",
        study_sources=tuple(sources),
        budget=RunBudget(request_limit=request_limit),
    )


@pytest.mark.spec(SPEC)
def test_calls_only_for_plan_candidates():
    plan = _plan(
        [
            {"candidate_id": "c1", "stages": {"synthesis": 2, "extraction": 1}},
            {"candidate_id": "c2", "stages": {"synthesis": 1, "extraction": 0}},
        ]
    )

    calls = list(budget.comparative_calls(plan))

    assert [(stage, candidate) for stage, candidate, _ in calls] == [
        ("synthesis", "c1"),
        ("synthesis", "c1"),
        ("extraction", "c1"),
        ("synthesis", "c2"),
    ]
    # Every call targets a plan candidate; nothing outside the plan builds.
    assert {candidate for _, candidate, _ in calls} <= {"c1", "c2"}
    for stage, _, batch in calls:
        assert stage in budget.COMPARATIVE_STAGES
        assert type(batch) is dict
        assert batch["candidate_id"] in {"c1", "c2"}
    # Discovery issues no model call.
    assert not any(stage == "discovery" for stage, _, _ in calls)


@pytest.mark.spec(SPEC)
def test_stage_budgets_stop_the_stage_and_the_ceiling_stops_the_run():
    # A zero stage budget issues no call for that stage.
    plan = _plan([{"candidate_id": "c1", "stages": {"synthesis": 2, "extraction": 0}}])
    assert [
        (stage, candidate) for stage, candidate, _ in budget.comparative_calls(plan)
    ] == [
        ("synthesis", "c1"),
        ("synthesis", "c1"),
    ]
    # Stage budgets beyond the plan's total ceiling refuse the plan.
    exceeded = _plan(
        [{"candidate_id": "c1", "stages": {"synthesis": 2, "extraction": 2}}],
        request_limit=3,
    )
    with pytest.raises(ConsumerRefused, match="comparative_budget_exhausted"):
        list(budget.comparative_calls(exceeded))


@pytest.mark.spec(SPEC)
def test_a_call_for_a_candidate_outside_the_plan_cannot_be_built():
    missing_id = _plan([{"stages": {"synthesis": 1, "extraction": 0}}])
    with pytest.raises(ValueError, match="needs its id"):
        list(budget.comparative_calls(missing_id))
    wrong_stages = _plan([{"candidate_id": "c1", "stages": {"synthesis": 1}}])
    with pytest.raises(ValueError, match="synthesis and extraction only"):
        list(budget.comparative_calls(wrong_stages))
