"""Draft a typed step graph from a skill document
(MERISTEM-HARNESS-CYCLE-R024.1)."""

from __future__ import annotations

import hashlib
import json

import ports_support as support
import pytest
from pydantic_ai.models.test import TestModel
from support.recorded_engine import RecordedEnginePort, params_digest

from meristem.graph import HarnessConfig, RunPlan, create_graph_agent, execute_graph
from meristem.harness import plan as harness_plan
from meristem.harness import run_lifecycle

SPEC = "MERISTEM-HARNESS-CYCLE-R024.1"
# spec: MERISTEM-HARNESS-CYCLE-R024.1

DOC = json.dumps({"steps": [{"type": "act"}, {"type": "verify"}]})
DIGEST = hashlib.sha256(DOC.encode("utf-8")).hexdigest()


async def _run_on_end(hook, *, port: RecordedEnginePort | None) -> dict:
    """One run whose admitted ``on_end`` consumer is ``hook``."""
    lifecycle = run_lifecycle.RunLifecycle(consumers=("skill_workflow",))
    lifecycle.add("skill_workflow", "on_end", hook)
    plan = RunPlan(
        model=TestModel(custom_output_text="done"),
        steps=("q",),
        lifecycle=lifecycle,
        harness=HarnessConfig(consumers=("skill_workflow",)),
    )
    support.register_run_decision_provider(lambda request: plan)
    if port is not None:
        support.register_engine_decision_port(port)
    graph, config = create_graph_agent(agent_model="m", ports=support.PORTS)
    return await execute_graph(graph, config, "q", run_id="r1", ports=support.PORTS)


@pytest.mark.spec(SPEC)
async def test_draft_submitted_with_digest():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "skill_workflow_compile",
                    "params": {
                        "skill_workflow": {
                            "steps": [{"type": "act"}, {"type": "verify"}]
                        },
                        "source_digest": DIGEST,
                    },
                    "answer": {"outcome": "committed", "record_digest": "prog-1"},
                }
            ]
        }
    )
    drafted: dict = {}

    async def draft(_state) -> None:
        drafted["draft"] = await harness_plan.draft_skill_workflow(
            DOC, support.PORTS.engine_decision_port(), run_ref="r1"
        )

    result = await _run_on_end(draft, port=port)

    assert result["status"] == "completed"
    # The submission carries the document digest with the graph.
    assert [method for method, _ in port.calls] == ["skill_workflow_compile"]
    assert port.calls[0] == (
        "skill_workflow_compile",
        params_digest(
            {
                "skill_workflow": {"steps": [{"type": "act"}, {"type": "verify"}]},
                "source_digest": DIGEST,
            }
        ),
    )
    draft = drafted["draft"]
    assert draft.source_digest == DIGEST
    assert draft.program_ref == "prog-1"


@pytest.mark.spec(SPEC)
async def test_step_without_type_refuses_skill_draft_invalid():
    port = RecordedEnginePort({"answers": []})
    bad = json.dumps({"steps": [{"prompt": "untitled"}]})
    drafted: dict = {}

    async def draft(_state) -> None:
        drafted["draft"] = await harness_plan.draft_skill_workflow(
            bad, support.PORTS.engine_decision_port(), run_ref="r1"
        )

    result = await _run_on_end(draft, port=port)

    # The refusal reaches the run at on_end, after the terminal status is
    # decided: the closed machine keeps the run completed and carries the
    # refusal as the run's error.
    assert result["status"] == "completed"
    assert "skill_draft_invalid" in (result["error"] or "")
    # The refusal happens before the submission: nothing was sent.
    assert port.calls == []


@pytest.mark.spec(SPEC)
async def test_document_without_steps_refuses_skill_draft_invalid():
    port = RecordedEnginePort({"answers": []})

    async def draft(_state) -> None:
        await harness_plan.draft_skill_workflow(
            json.dumps({"prompt": "untitled"}),
            support.PORTS.engine_decision_port(),
            run_ref="r1",
        )

    result = await _run_on_end(draft, port=port)

    assert result["status"] == "completed"
    assert "skill_draft_invalid" in (result["error"] or "")
    assert port.calls == []


@pytest.mark.spec(SPEC)
async def test_engine_refusal_passes_through():
    doc = json.dumps({"steps": [{"type": "act"}]})
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "skill_workflow_compile",
                    "params": {
                        "skill_workflow": {"steps": [{"type": "act"}]},
                        "source_digest": hashlib.sha256(
                            doc.encode("utf-8")
                        ).hexdigest(),
                    },
                    "answer": {
                        "outcome": "abstained",
                        "refusal_code": "SKILL_COMPILE_REFUSED",
                    },
                }
            ]
        }
    )

    async def draft(_state) -> None:
        await harness_plan.draft_skill_workflow(
            doc, support.PORTS.engine_decision_port(), run_ref="r1"
        )

    result = await _run_on_end(draft, port=port)

    assert result["status"] == "completed"
    assert "SKILL_COMPILE_REFUSED" in (result["error"] or "")


@pytest.mark.spec(SPEC)
async def test_unbound_port_refuses_and_decides_nothing():
    async def draft(_state) -> None:
        await harness_plan.draft_skill_workflow(
            DOC, support.PORTS.engine_decision_port(), run_ref="r1"
        )

    result = await _run_on_end(draft, port=None)

    # The unbound port refuses at on_end, after the terminal status is
    # decided: the run keeps it and carries the refusal as its error.
    assert result["status"] == "completed"
    assert "engine_decision_unavailable" in (result["error"] or "")
