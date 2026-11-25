"""Pin the champion revision in the cycle (MERISTEM-HARNESS-CYCLE-R044.1)."""

from __future__ import annotations

from typing import Any

import ports_support as support
import pytest
from support.recorded_engine import RecordedEnginePort, params_digest

import meristem.graph.harness_cycle as cycle
from meristem.graph import HarnessConfig, RunBudget, RunPlan
from meristem.graph.harness_cycle import run_cycle_tick
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineDecisionRefused

POINTER_PARAMS = {"pointer_key": "champion:classification:task-1"}


def _request():
    return support.request(
        task="classify",
        run_id="task-1",
        metadata={"harness_seed": "seed-1", "harness_current_version_ref": "v-0"},
    )


def _plan(consumers: tuple[str, ...] = ("champion_pin",)) -> RunPlan:
    return RunPlan(
        model="m-run",
        budget=RunBudget(candidate_slots=2),
        harness=HarnessConfig(consumers=consumers),
    )


def _contrast_params() -> dict[str, Any]:
    plan = _plan()
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


def _pointer_answer(revision_ref: str) -> dict[str, Any]:
    return {"outcome": "solved", "payload": {"revision_ref": revision_ref}}


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R044.1")
async def test_cycle_pins_champion_revision(events, tmp_path) -> None:
    """A pointer that moves mid-tick: the tick holds the first revision
    and refuses at persist; nothing adopts, nothing persists."""
    component = tmp_path / ".harness" / "guardrails.md"
    component.parent.mkdir(parents=True)
    component.write_text("current guardrails", encoding="utf-8")
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "pointer",
                    "params": POINTER_PARAMS,
                    "answer": _pointer_answer("rev-1"),
                },
                {
                    "method": "harness_contrast",
                    "params": _contrast_params(),
                    "answer": {
                        "outcome": "committed",
                        "record_digest": "contrast-record-pin",
                        "payload": {
                            "adopted_version_ref": "v-b",
                            "adopted_component_kind": "guardrails",
                            "adopted_content": "tighter guardrails",
                        },
                    },
                },
                {
                    "method": "pointer",
                    "params": POINTER_PARAMS,
                    "answer": _pointer_answer("rev-2"),
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
        _plan(),
        task_class="classification",
        graph=object(),
        library_query=lambda _class: [
            {"component_kind": "system_prompt", "component_version_ref": "v-a"},
            {"component_kind": "guardrails", "component_version_ref": "v-b"},
        ],
        write_envelope=lambda envelope: "ref",
        root=tmp_path,
    )

    assert outcome.abstain_reason == "champion_moved"
    assert outcome.adopted_version_ref is None
    # The pointer is read once before propose and once again at persist;
    # the adoption is never committed.
    pointer_digest = params_digest(POINTER_PARAMS)
    assert port.calls == [
        ("pointer", pointer_digest),
        ("harness_contrast", params_digest(_contrast_params())),
        ("pointer", pointer_digest),
    ]
    assert all(call[0] != "log" for call in port.calls)
    assert component.read_text(encoding="utf-8") == "current guardrails"
    # The pinned revision the tick held enters the tick trace.
    assert events[0]["champion_pin"] == "rev-1"
    assert events[0]["abstain_reason"] == "champion_moved"


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R044.1")
async def test_champion_pin_refusals_and_gate(events, tmp_path) -> None:
    plan = _plan()

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

    # Engine codes pass through the port boundary at the pin.
    for code in ("PROMOTION_CAS_CONFLICT", "PROMOTION_RECEIPT_MISMATCH"):
        support.register_engine_decision_port(
            RecordedEnginePort(
                {
                    "answers": [
                        {
                            "method": "pointer",
                            "params": POINTER_PARAMS,
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

    # An absent pointer refuses champion_missing.
    support.register_engine_decision_port(
        RecordedEnginePort(
            {
                "answers": [
                    {
                        "method": "pointer",
                        "params": POINTER_PARAMS,
                        "answer": {"outcome": "solved", "payload": {}},
                    }
                ]
            }
        )
    )
    with pytest.raises(ConsumerRefused, match="champion_missing"):
        await run_cycle_tick(_request(), plan, **kwargs)

    # With no bound port the step refuses engine_decision_unavailable
    # and decides nothing.
    support.register_engine_decision_port(None)
    with pytest.raises(EngineDecisionRefused, match="engine_decision_unavailable"):
        await run_cycle_tick(_request(), plan, **kwargs)
    assert events == []

    # The consumer gate: without the name in the run plan's consumers the
    # tick reads no pointer and decides no pin.
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "harness_contrast",
                    "params": _contrast_params(),
                    "answer": {"outcome": "abstained"},
                }
            ]
        }
    )
    support.register_engine_decision_port(port)
    outcome = await run_cycle_tick(_request(), _plan(consumers=()), **kwargs)
    assert outcome.abstain_reason == "compose_abstention"
    assert [call[0] for call in port.calls] == ["harness_contrast"]
    assert events[-1]["champion_pin"] == ""


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R044.1")
async def test_stable_pointer_lets_the_tick_persist(events, tmp_path) -> None:
    """The pointer that does not move: the tick pins, persists and
    reports the pinned revision in the trace."""
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "pointer",
                    "params": POINTER_PARAMS,
                    "answer": _pointer_answer("rev-1"),
                },
                {
                    "method": "harness_contrast",
                    "params": _contrast_params(),
                    "answer": {
                        "outcome": "committed",
                        "record_digest": "contrast-record-stable",
                        "payload": {
                            "adopted_version_ref": "v-b",
                            "adopted_component_kind": "guardrails",
                            "adopted_content": "tighter guardrails",
                        },
                    },
                },
                {
                    "method": "pointer",
                    "params": POINTER_PARAMS,
                    "answer": _pointer_answer("rev-1"),
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
        _plan(),
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
    pointer_digest = params_digest(POINTER_PARAMS)
    assert port.calls == [
        ("pointer", pointer_digest),
        ("harness_contrast", params_digest(_contrast_params())),
        ("pointer", pointer_digest),
        ("log", params_digest({"op": "HarnessAdoption"})),
    ]
    assert (tmp_path / ".harness" / "guardrails.md").read_text(
        encoding="utf-8"
    ) == "tighter guardrails"
    assert events[0]["champion_pin"] == "rev-1"
