"""Run outcomes reported to the engine (R004.1, R025.1, R041.2, R049.1, R051.1)."""

from __future__ import annotations

import pytest
from support.scripted_port import ScriptedPort, answer

from meristem.graph.state import GraphState, RunStatus
from meristem.harness import run_outcomes as ro
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineDecisionRefused, ToolStepResult


def _state(status: RunStatus) -> GraphState:
    state = GraphState(run_id="r1", query="q")
    state.status = status
    return state


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R004.1")
@pytest.mark.parametrize(
    ("status", "success"),
    [
        (RunStatus.COMPLETED, True),
        (RunStatus.FAILED, False),
        (RunStatus.STOPPED_BUDGET, None),
    ],
)
async def test_one_evaluation_per_applied_decision(status, success):
    port = ScriptedPort()
    state = _state(status)
    state.note_record("agent_assemble", "dig-1")
    trace = await ro.report_outcomes(state, port)
    assert trace == [{"evaluation_id": "r1:agent_assemble", "refusal_code": None}]
    assert port.blobs == [
        {"record_id": "dig-1", "evaluation_id": "r1:agent_assemble", "success": success}
    ]
    assert port.names("log") == ["Evaluate"]


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R004.1")
async def test_refused_evaluation_keeps_the_run_result_and_no_port_refuses():
    state = _state(RunStatus.COMPLETED)
    state.note_record("k", "d")
    port = ScriptedPort(Evaluate=answer(refusal="EVALUATION_UNKNOWN"))
    trace = await ro.report_outcomes(state, port)
    assert trace[0]["refusal_code"] == "EVALUATION_UNKNOWN"
    assert state.status is RunStatus.COMPLETED
    with pytest.raises(EngineDecisionRefused):
        await ro.report_outcomes(state, None)


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R025.1")
async def test_tool_outcome_cites_the_routing_record():
    port = ScriptedPort()
    ok = ToolStepResult(result=1, routing_record_digest="route-1")
    assert (
        await ro.evaluate_tool_outcome(ok, port, run_id="r1", task_id="t1") == "r1:t1"
    )
    refused = ToolStepResult(
        outcome="refused", error="no", routing_record_digest="route-2"
    )
    await ro.evaluate_tool_outcome(refused, port, run_id="r1", task_id="t2")
    skipped = await ro.evaluate_tool_outcome(
        ToolStepResult(result=1), port, run_id="r1", task_id="t3"
    )
    assert skipped is None
    assert [b["success"] for b in port.blobs] == [True, None]
    assert port.blobs[0]["record_id"] == "route-1"


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R041.2")
async def test_experiences_recorded_as_outcomes():
    port = ScriptedPort()
    items = [
        {"subject_ref": "a1", "kind": "action", "success": True, "cost": 3},
        {
            "subject_ref": "t1",
            "kind": "task",
            "success": False,
            "cost": 9,
            "evidence_ref": "e",
        },
    ]
    outcomes = await ro.record_experience(_state(RunStatus.COMPLETED), items, port)
    assert [o["kind"] for o in outcomes] == ["action", "task"]
    assert port.names("log") == ["evaluate", "evaluate"]
    with pytest.raises(ConsumerRefused) as raised:
        await ro.record_experience(
            _state(RunStatus.COMPLETED), [{"kind": "task"}], port
        )
    assert raised.value.code == "experience_unkeyed"


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R051.1")
async def test_skill_outcome_carries_the_package_digest():
    port = ScriptedPort()
    body = await ro.record_skill_outcome(
        _state(RunStatus.COMPLETED), port, skill_ref="s", cost=2, package_digest="pkg"
    )
    assert port.blobs == [body] and body["package_digest"] == "pkg"
    with pytest.raises(ConsumerRefused) as raised:
        await ro.record_skill_outcome(
            _state(RunStatus.COMPLETED),
            port,
            skill_ref="s",
            cost=2,
            package_digest=None,
        )
    assert raised.value.code == "package_digest_missing"


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R049.1")
async def test_receipt_only_for_a_local_model_with_attestation():
    port = ScriptedPort(DecisionEval=answer())
    state = _state(RunStatus.COMPLETED)
    common = {"model_ref": "m", "task_digest": "t"}
    assert (
        await ro.emit_local_model_receipt(
            state, port, local=False, attestation_ref=None, **common
        )
        is None
    )
    sent = await ro.emit_local_model_receipt(
        state, port, local=True, attestation_ref="att", **common
    )
    assert sent is not None and port.names("write") == ["DecisionEval"]
    with pytest.raises(ConsumerRefused) as raised:
        await ro.emit_local_model_receipt(
            state, port, local=True, attestation_ref=None, **common
        )
    assert raised.value.code == "attestation_missing"
