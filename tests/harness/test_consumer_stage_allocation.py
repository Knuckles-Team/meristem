"""The stage allocation consumer: one decide per tick, one cost entry per stage."""

from __future__ import annotations

from typing import Any

import ports_support as support
import pytest
from support.recorded_engine import RecordedEnginePort, params_digest

import meristem.graph.harness_cycle as cycle
from meristem.graph import HarnessConfig, RunBudget, RunPlan
from meristem.graph.harness_cycle import (
    STAGE_ALLOCATION_KIND,
    STAGES,
    run_cycle_tick,
)
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineDecisionRefused


def _request():
    return support.request(
        task="classify",
        run_id="task-1",
        metadata={"harness_seed": "seed-1", "harness_current_version_ref": "v-0"},
    )


def _plan(consumers: tuple[str, ...] = ()) -> RunPlan:
    return RunPlan(
        model="m-run",
        budget=RunBudget(candidate_slots=2),
        harness=HarnessConfig(consumers=consumers),
    )


def _stage_params(plan: RunPlan) -> dict[str, Any]:
    return {
        "task_ref": "task-1",
        "budget_ref": cycle._budget_ref(plan.budget),
        "stages": list(STAGES),
    }


def _contrast_params(plan: RunPlan) -> dict[str, Any]:
    digest = cycle.conditions_digest(
        task="classify",
        seed="seed-1",
        model_ref=cycle._model_ref(plan.model),
        budget_ref=cycle._budget_ref(plan.budget),
    )
    return {
        "task_ref": "task-1",
        "task_class": "classification",
        "conditions_digest": digest,
        "candidate_version_refs": ["v-a", "v-b"],
        "baseline_trace_id": "task-1:probe:current",
        "probe_trace_ids": [
            "task-1:probe:task-1:candidate:0",
            "task-1:probe:task-1:candidate:1",
        ],
    }


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R042.1")
async def test_stage_uses_allocated_model_and_records_cost(events, tmp_path) -> None:
    """One StageAllocation decide per tick; every stage names its model and
    cost in the tick trace the reflect event carries."""
    plan = _plan(consumers=("stage_allocation",))
    stage_models = {
        "propose": "m-propose",
        "probe": "m-probe",
        "compose": "m-compose",
        "persist": "m-persist",
        "reflect": "m-reflect",
    }
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": STAGE_ALLOCATION_KIND,
                    "params": _stage_params(plan),
                    "answer": {
                        "outcome": "committed",
                        "payload": {"stage_models": stage_models},
                    },
                },
                {
                    "method": "harness_contrast",
                    "params": _contrast_params(plan),
                    "answer": {
                        "outcome": "committed",
                        "record_digest": "contrast-record-sa",
                        "payload": {
                            "adopted_version_ref": "v-b",
                            "adopted_component_kind": "guardrails",
                            "adopted_content": "tighter guardrails",
                        },
                    },
                },
                {
                    "method": "log",
                    "params": {"op": "HarnessAdoption"},
                    "answer": {
                        "outcome": "committed",
                        "payload": {
                            "adoption_ref": "eg:adoption/1",
                            "contrast_ref": "eg:contrast/1",
                            "lesson_ref": "eg:lesson/1",
                        },
                    },
                },
            ]
        }
    )
    support.register_engine_decision_port(port)

    async def host_probe(**kwargs):
        return {"run_id": kwargs["run_id"], "status": "completed", "steps": 1}

    support.register_harness_probe_provider(host_probe)

    outcome = await run_cycle_tick(
        _request(),
        plan,
        task_class="classification",
        graph=object(),
        library_query=lambda _class: [
            {"component_kind": "system_prompt", "component_version_ref": "v-a"},
            {"component_kind": "guardrails", "component_version_ref": "v-b"},
        ],
        write_envelope=lambda envelope: "ref",
        root=tmp_path,
    )

    assert outcome.abstain_reason is None
    assert outcome.adopted_version_ref == "eg:adoption/1"
    # One StageAllocation decide per tick, before the first stage.
    assert port.calls[0] == (STAGE_ALLOCATION_KIND, params_digest(_stage_params(plan)))
    assert [call[0] for call in port.calls].count(STAGE_ALLOCATION_KIND) == 1
    # One cost entry per stage, in stage order, each naming the model the
    # allocation ran it on.
    assert events[0]["type"] == "harness_cycle_outcome"
    assert events[0]["stage_allocations"] == [
        {"stage": "propose", "model_ref": "m-propose", "tokens": 0, "cost": 0},
        {"stage": "probe", "model_ref": "m-probe", "tokens": 0, "cost": 0},
        {"stage": "compose", "model_ref": "m-compose", "tokens": 0, "cost": 0},
        {"stage": "persist", "model_ref": "m-persist", "tokens": 0, "cost": 0},
        {"stage": "reflect", "model_ref": "m-reflect", "tokens": 0, "cost": 0},
    ]


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R042.1")
async def test_stage_allocation_refusals_keep_harness(events, tmp_path) -> None:
    component = tmp_path / ".harness" / "guardrails.md"
    component.parent.mkdir(parents=True)
    component.write_text("current guardrails", encoding="utf-8")
    plan = _plan(consumers=("stage_allocation",))

    async def host_probe(**kwargs):
        return {"run_id": kwargs["run_id"], "status": "completed", "steps": 1}

    support.register_harness_probe_provider(host_probe)
    kwargs = dict(
        task_class="classification",
        graph=object(),
        library_query=lambda _class: [
            {"component_kind": "system_prompt", "component_version_ref": "v-a"},
            {"component_kind": "guardrails", "component_version_ref": "v-b"},
        ],
        write_envelope=lambda envelope: "ref",
        root=tmp_path,
    )

    # An absent port fails closed before any stage: nothing runs, nothing
    # persists, the harness is kept.
    support.register_engine_decision_port(None)
    with pytest.raises(EngineDecisionRefused, match="engine_decision_unavailable"):
        await run_cycle_tick(_request(), plan, **kwargs)
    assert events == []
    assert component.read_text(encoding="utf-8") == "current guardrails"

    # Engine refusal codes pass through the port boundary.
    for code in ("ALLOCATION_BUDGET_BELOW_MINIMUM", "ALLOCATION_NO_ADEQUATE_MODEL"):
        support.register_engine_decision_port(
            RecordedEnginePort(
                {
                    "answers": [
                        {
                            "method": STAGE_ALLOCATION_KIND,
                            "params": _stage_params(plan),
                            "answer": {
                                "outcome": "abstained",
                                "refusal_code": code,
                            },
                        }
                    ]
                }
            )
        )
        with pytest.raises(ConsumerRefused, match=code):
            await run_cycle_tick(_request(), plan, **kwargs)
    assert events == []

    # An abstaining engine leaves the tick without an allocation; the tick
    # refuses allocation_missing and records no stage entries.
    support.register_engine_decision_port(
        RecordedEnginePort(
            {
                "answers": [
                    {
                        "method": STAGE_ALLOCATION_KIND,
                        "params": _stage_params(plan),
                        "answer": {"outcome": "abstained"},
                    }
                ]
            }
        )
    )
    outcome = await run_cycle_tick(_request(), plan, **kwargs)
    assert outcome.abstain_reason == "allocation_missing"
    assert events[0]["stage_allocations"] == []
    assert component.read_text(encoding="utf-8") == "current guardrails"

    # A stage missing from the allocation refuses when its turn comes,
    # after the earlier stages recorded their entries.
    support.register_engine_decision_port(
        RecordedEnginePort(
            {
                "answers": [
                    {
                        "method": STAGE_ALLOCATION_KIND,
                        "params": _stage_params(plan),
                        "answer": {
                            "outcome": "committed",
                            "payload": {
                                "stage_models": {
                                    "propose": "m-1",
                                    "compose": "m-2",
                                    "persist": "m-3",
                                    "reflect": "m-4",
                                }
                            },
                        },
                    }
                ]
            }
        )
    )
    outcome = await run_cycle_tick(_request(), plan, **kwargs)
    assert outcome.abstain_reason == "allocation_missing"
    assert events[1]["stage_allocations"] == [
        {"stage": "propose", "model_ref": "m-1", "tokens": 0, "cost": 0}
    ]

    # The consumer gate: without the name in the run plan's consumers the
    # tick decides no allocation and records no stage entries.
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "harness_contrast",
                    "params": _contrast_params(_plan()),
                    "answer": {"outcome": "abstained"},
                }
            ]
        }
    )
    support.register_engine_decision_port(port)
    outcome = await run_cycle_tick(_request(), _plan(), **kwargs)
    assert outcome.abstain_reason == "compose_abstention"
    assert [call[0] for call in port.calls] == ["harness_contrast"]
    assert events[2]["stage_allocations"] == []
