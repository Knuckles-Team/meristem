"""Submit a task plan when no route stage matches
(MERISTEM-HARNESS-CYCLE-R006.2)."""

from __future__ import annotations

import json

import ports_support as support
import pytest
from support.recorded_engine import RecordedEnginePort, params_digest

from meristem.harness import plan as harness_plan

SPEC = "MERISTEM-HARNESS-CYCLE-R006.2"
# spec: MERISTEM-HARNESS-CYCLE-R006.2

TASKS = {"tasks": [{"id": "t1", "prompt": "do it", "depends_on": []}]}


@pytest.mark.spec(SPEC)
async def test_no_stage_submits_task_plan():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "task_plan",
                    "params": TASKS,
                    "answer": {
                        "outcome": "solved",
                        "payload": {"plan": {"steps": ["t1"]}},
                        "record_digest": "rec-1",
                    },
                }
            ]
        }
    )
    support.register_engine_decision_port(port)
    request = support.request(
        task="t",
        run_id="r1",
        metadata={"route_refusal": "ROUTE_NO_STAGE", "model_output": json.dumps(TASKS)},
    )

    submission = await harness_plan.submit_task_plan_on_no_stage(
        request, support.PORTS.engine_decision_port(), run_ref="r1"
    )

    assert port.calls == [("task_plan", params_digest(TASKS))]
    assert submission is not None
    assert submission.tasks == ({"id": "t1", "prompt": "do it", "depends_on": []},)
    # The run runs the plan the answer returned.
    assert submission.plan == {"steps": ["t1"]}
    assert submission.record_digest == "rec-1"


@pytest.mark.spec(SPEC)
async def test_model_output_without_tasks_refuses_plan_extraction_failed():
    port = RecordedEnginePort({"answers": []})
    support.register_engine_decision_port(port)
    request = support.request(
        task="t",
        run_id="r1",
        metadata={"route_refusal": "ROUTE_NO_STAGE", "model_output": "{}"},
    )

    with pytest.raises(Exception, match="plan_extraction_failed"):
        await harness_plan.submit_task_plan_on_no_stage(
            request, support.PORTS.engine_decision_port(), run_ref="r1"
        )

    assert port.calls == []


@pytest.mark.spec(SPEC)
async def test_other_engine_codes_pass_through_without_submission():
    port = RecordedEnginePort({"answers": []})
    support.register_engine_decision_port(port)
    request = support.request(
        task="t",
        run_id="r1",
        metadata={
            "route_refusal": "ROUTE_AMBIGUOUS",
            "model_output": json.dumps(TASKS),
        },
    )

    submission = await harness_plan.submit_task_plan_on_no_stage(
        request, support.PORTS.engine_decision_port(), run_ref="r1"
    )

    assert submission is None
    assert port.calls == []


@pytest.mark.spec(SPEC)
async def test_unbound_port_refuses_and_decides_nothing():
    request = support.request(
        task="t",
        run_id="r1",
        metadata={"route_refusal": "ROUTE_NO_STAGE", "model_output": json.dumps(TASKS)},
    )

    with pytest.raises(Exception, match="engine_decision_unavailable"):
        await harness_plan.submit_task_plan_on_no_stage(
            request, support.PORTS.engine_decision_port(), run_ref="r1"
        )
