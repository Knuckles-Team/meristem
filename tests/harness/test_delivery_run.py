"""One delivery assignment run applies received feedback (MERISTEM-SCM-R012.1 to R012.7)."""

from __future__ import annotations

import dataclasses
from dataclasses import replace

import pytest
from support.coding_fakes import Gates, Heads
from support.scripted_port import ScriptedPort, answer

from meristem.harness.changeset_loop import Submitted
from meristem.harness.delivery_run import (
    DEFAULT_BACKGROUND_LIMIT_S,
    DeliveryAssignmentContext,
    DeliveryReportOut,
    DeliveryRun,
    deliver_inputs,
    dispose_thread,
    drain_background,
    reconcile,
    report,
    revise_proposal,
)
from meristem.harness.edit_engine import Edit
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import (
    ActiveChildBinding,
    ActiveChildMessage,
    HostPorts,
    ToolStepResult,
)
from meristem.runtime.local_workspace import LocalWorkspace
from meristem.runtime.workspace import DevWorkspace

CONTEXT = DeliveryAssignmentContext(
    "d1",
    ("ROW-1",),
    "pr-7",
    round_verdicts={"bot": "stop_addressing"},
    background_limit_s=60,
)


def _run(
    context=CONTEXT, calls=None, results=None, port=None, **ports
) -> tuple[DeliveryRun, list]:
    events: list[dict] = []

    async def caller(server, tool, arguments, task_id):
        if calls is not None:
            calls.append((tool, arguments))
        outcome = (results or {}).get(tool, ToolStepResult(result={}))
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    host = HostPorts(events=events.append, caller=caller, engine_decision=port, **ports)
    return DeliveryRun(context, host), events


@pytest.mark.spec("MERISTEM-SCM-R012.1")
def test_context_and_report_are_frozen_and_state_set_is_closed():
    with pytest.raises(dataclasses.FrozenInstanceError):
        CONTEXT.delivery_id = "x"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        DeliveryReportOut("ready").state = "x"  # type: ignore[misc]
    for state in ("progress", "waiting", "ready", "blocked", "failed", "dropped"):
        assert DeliveryReportOut(state).state == state
    with pytest.raises(ConsumerRefused) as refused:
        DeliveryReportOut("merged")
    assert refused.value.code == "delivery_report_state_unknown"


class _Inputs:
    binding = ActiveChildBinding("c", "p", "a", 1)

    def __init__(self, batches):
        self.batches, self.applied = list(batches), []

    async def take_ready(self):
        return tuple(self.batches.pop(0)) if self.batches else ()

    async def report_applied(self, message):
        self.applied.append(message.message_id)


def _message(ident: str) -> ActiveChildMessage:
    return ActiveChildMessage(_Inputs.binding, ident, f"e-{ident}", "text")


@pytest.mark.spec("MERISTEM-SCM-R012.2")
async def test_inputs_apply_between_turns_in_receipt_order():
    run, _ = _run()
    port = _Inputs([[_message("m1"), _message("m2")], [_message("m3")]])
    order: list[str] = []

    async def apply(message):
        assert port.applied == order
        order.append(message.message_id)

    assert await deliver_inputs(run, port, apply) == 2
    assert order == ["m1", "m2"] and port.applied == ["m1", "m2"]
    run.terminal = True
    with pytest.raises(ConsumerRefused) as refused:
        await deliver_inputs(run, port, apply)
    assert refused.value.code == "delivery_input_after_terminal"


class _Source(Heads):
    def __init__(self):
        super().__init__()
        self.submits = 0

    async def materialize(self, head, workspace):
        await super().materialize(head, workspace)
        (workspace.backend.root / "a.txt").write_text("before\n")

    async def submit(self, pending):
        self.submits += 1
        return Submitted("cs-2")


async def _revise(run, source):
    workspace = DevWorkspace(LocalWorkspace(), run_id="r")
    try:
        return await revise_proposal(
            run,
            workspace,
            (Edit("a.txt", "before", "after"),),
            base_head="head-0",
            fast_gates=("t",),
            submitter=source,
            gate_runner=Gates({"t": (0, "")}, files={"a.txt": "after\n"}),
            run_ref="d1",
        )
    finally:
        await workspace.stop()


@pytest.mark.spec("MERISTEM-SCM-R012.3")
async def test_ci_failure_revises_same_proposal_after_disarm():
    port = ScriptedPort(merge_arming=answer({"verdict": "disarm"}))
    run, events = _run(port=port)
    source = _Source()
    result = await _revise(run, source)
    assert result.reason == "submitted" and source.submits == 1
    assert events[0] == {"kind": "delivery_disarm", "proposal_ref": "pr-7"}
    assert run.proposal_exists and run.context.proposal_ref == "pr-7"
    assert port.calls[0][2] == {"delivery_id": "d1", "proposal_ref": "pr-7"}


@pytest.mark.spec("MERISTEM-SCM-R012.3")
async def test_missing_arming_verdict_pushes_nothing():
    run, _ = _run(port=ScriptedPort(merge_arming=answer({})))
    source = _Source()
    with pytest.raises(ConsumerRefused) as refused:
        await _revise(run, source)
    assert refused.value.code == "delivery_arming_verdict_missing"
    assert source.submits == 0 and source.heads == []


@pytest.mark.spec("MERISTEM-SCM-R012.4")
async def test_fixed_resolves_declined_replies_once_and_stop_verdict_halts():
    calls: list = []
    run, _ = _run(calls=calls, results={"thread.replies": ToolStepResult(result=[])})
    assert await dispose_thread(run, {"thread_id": "t1"}, "fixed") is None
    assert (
        await dispose_thread(
            run, {"thread_id": "t2"}, "declined", reply_reason="out of scope"
        )
        is None
    )
    assert (
        await dispose_thread(run, {"thread_id": "t2"}, "declined", reply_reason="again")
        is None
    )
    assert [c[0] for c in calls] == ["thread.resolve", "thread.replies", "thread.reply"]
    assert (
        await dispose_thread(
            run,
            {"thread_id": "t3", "reviewer": "bot"},
            "fixed",
            reviewer_is_automated=True,
        )
        == "stop_addressing"
    )
    assert await dispose_thread(run, {"thread_id": "t4"}, "open") is None
    assert len(calls) == 3
    refused, _ = _run(
        results={"thread.resolve": ToolStepResult(outcome="refused", error="no")}
    )
    assert (
        await dispose_thread(refused, {"thread_id": "t5"}, "fixed")
        == "delivery_disposition_refused"
    )
    already, _ = _run(
        calls=calls, results={"thread.replies": ToolStepResult(result=["r"])}
    )
    await dispose_thread(already, {"thread_id": "t6"}, "declined")
    assert "thread.reply" not in [c[0] for c in calls[3:]]


@pytest.mark.spec("MERISTEM-SCM-R012.5")
def test_each_report_state_and_single_question():
    run, events = _run()
    assert (
        report(run, "progress", "working", question="which branch?").question
        == "which branch?"
    )
    assert (
        report(run, "progress", "again", question="which file?").question
        == "which file?"
    )
    assert [e["question"] for e in events] == ["which branch?", "which file?"]
    with pytest.raises(ConsumerRefused):
        report(
            run,
            "ready",
            "done",
            reviewed=True,
            gates_passed=True,
            background_running=["lint"],
        )
    with pytest.raises(ConsumerRefused):
        report(run, "blocked", "")
    assert (
        report(run, "blocked", "needs a token").state == "blocked" and not run.terminal
    )
    assert (
        report(run, "ready", "done", reviewed=True, gates_passed=True).state == "ready"
    )
    assert run.terminal
    nobody = DeliveryRun(
        DeliveryAssignmentContext("d2"), run.ports, proposal_exists=True
    )
    with pytest.raises(ConsumerRefused) as refused:
        report(nobody, "progress")
    assert refused.value.code == "delivery_report_proposal_missing"
    assert report(nobody, "progress", proposal_ref="pr-9").proposal_ref == "pr-9"


@pytest.mark.spec("MERISTEM-SCM-R012.6")
async def test_recovery_note_skips_effects_that_already_landed():
    calls: list = []
    context = replace(CONTEXT, recovery_note="lane lost")
    log = ToolStepResult(result={"effects": ["fixed:t1"]})
    run, _ = _run(context, calls=calls, results={"changeset.log": log})
    assert await reconcile(run) == {"fixed:t1"}
    assert [c[0] for c in calls] == ["proposal.read", "changeset.log"]
    await dispose_thread(run, {"thread_id": "t1"}, "fixed")
    assert [c[0] for c in calls] == ["proposal.read", "changeset.log"]
    plain, _ = _run(calls=calls)
    assert await reconcile(plain) == set()
    broken, _ = _run(context, results={"proposal.read": RuntimeError("down")})
    with pytest.raises(ConsumerRefused) as refused:
        await reconcile(broken)
    assert refused.value.code == "delivery_recovery_read_failed"


@pytest.mark.spec("MERISTEM-SCM-R012.7")
async def test_background_limit_stops_jobs_and_reports_waiting():
    stopped: list[str] = []
    waited: list[float] = []

    async def wait(jobs, limit):
        waited.append(limit)
        return {"lint"}

    async def stop(name):
        stopped.append(name)

    run, events = _run()
    got = await drain_background(run, ["lint", "tests"], wait, stop)
    assert waited == [60.0] and stopped == ["tests"]
    assert got is not None and got.state == "waiting" and "tests" in got.summary
    assert events[-1]["state"] == "waiting"
    default, _ = _run(replace(CONTEXT, background_limit_s=None))
    assert await drain_background(default, ["lint"], wait, stop) is None
    assert waited[-1] == float(DEFAULT_BACKGROUND_LIMIT_S)
    bad, _ = _run(replace(CONTEXT, background_limit_s=0))
    with pytest.raises(ConsumerRefused) as refused:
        await drain_background(bad, [], wait, stop)
    assert refused.value.code == "delivery_background_limit_invalid"
