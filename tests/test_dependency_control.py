"""Control steps of a received plan: branch, join, decision and approval wait.

Every test drives the public run API with fake host ports. Waits are gated by
events the test owns; no test sleeps or reads a wall clock.
"""

from __future__ import annotations

import asyncio
import contextlib
import json

import ports_support as support
import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from meristem.graph import (
    ApprovalWaiterUnavailable,
    DecisionPortUnavailable,
    DependencyPlan,
    RunPlan,
    RunStatus,
    RunTask,
    ToolStep,
    ToolStepResult,
    iterate_run,
    run,
)
from meristem.graph.dependency import (
    arm_closure,
    transition_task,
    validate_dependency_plan,
)
from meristem.graph.state import TaskEvent, TaskStatus, TaskTransitionRefused
from meristem.host_ports import ApprovalOutcome, DecisionVerdict, HostPorts

SPEC_1 = "MERISTEM-RUN-R018.1"
SPEC_2 = "MERISTEM-RUN-R018.2"
SPEC_3 = "MERISTEM-RUN-R018.3"
SPEC_4 = "MERISTEM-RUN-R018.4"
SPEC_5 = "MERISTEM-RUN-R018.5"
SPEC_6 = "MERISTEM-RUN-R018.6"
SPEC_7 = "MERISTEM-RUN-R018.7"


def tool(task_id: str, *deps: str, arguments=None) -> RunTask:
    return RunTask(
        task_id, depends_on=deps, call=ToolStep("srv", "work", arguments or {})
    )


def branch(task_id: str, kind: str, *targets: str, deps=()) -> RunTask:
    return RunTask(
        task_id,
        depends_on=tuple(deps),
        branch={
            "kind": kind,
            "arms": [{"target_task_id": target} for target in targets],
        },
    )


def join(task_id: str, source: str, *deps: str) -> RunTask:
    return RunTask(task_id, depends_on=deps, join={"branch_task_id": source})


def approval(task_id: str, *deps: str, timeout_s=30) -> RunTask:
    return RunTask(
        task_id,
        depends_on=deps,
        approval={"approver_role": "owner", "timeout_s": timeout_s},
    )


def plan_of(tasks, output, capacity=2) -> RunPlan:
    async def never(messages, info):
        return ModelResponse(parts=[TextPart("unexpected")])

    return RunPlan(
        model=FunctionModel(never),
        dependency_plan=DependencyPlan(
            tasks=tuple(tasks),
            output_task_id=output,
            max_concurrency=capacity,
            plan_id="sha256:plan",
        ),
    )


class Fakes:
    """Fake tool caller, decision port and approval waiter that record calls."""

    def __init__(self, verdicts=None, outcome=None, gate=None, decide_gate=None):
        self.called: list[str] = []
        self.asked: list[tuple[str, dict, str]] = []
        self.waits: list[tuple[str, str, str, int]] = []
        self.verdicts = verdicts or {}
        self.outcome = outcome or ApprovalOutcome("approve", "alice", 1234)
        self.gate = gate
        self.decide_gate = decide_gate
        self.progress = asyncio.Event()
        support.PORTS.caller = self.caller
        support.PORTS.decision = self.decide
        support.PORTS.approval = self.wait

    async def caller(self, server, tool_name, arguments, task_id):
        self.called.append(task_id)
        self.progress.set()
        return ToolStepResult(result={"task": task_id, **arguments})

    async def until(self, condition):
        """Block on host-side progress until ``condition()`` holds (bounded)."""
        while not condition():
            self.progress.clear()
            await asyncio.wait_for(self.progress.wait(), timeout=0.5)

    async def decide(self, question, payload, task_id):
        self.asked.append((question, dict(payload), task_id))
        if self.decide_gate is not None:
            await self.decide_gate.wait()
        verdict = self.verdicts[task_id]
        return verdict(payload) if callable(verdict) else verdict

    async def wait(self, run_id, task_id, role, timeout_s):
        self.waits.append((run_id, task_id, role, timeout_s))
        self.progress.set()
        if self.gate is not None:
            await self.gate.wait()
        return self.outcome


def kinds(events, kind):
    return [event for event in events if event["type"] == kind]


@pytest.mark.spec(SPEC_1)
def test_control_fields_validate_and_ports_register():
    ports = HostPorts()
    assert ports.decision_port() is None and ports.approval_waiter() is None

    async def decide(question, payload, task_id):
        return DecisionVerdict()

    async def wait(run_id, task_id, role, timeout_s):
        return ApprovalOutcome("approve")

    bound = HostPorts(decision=decide, approval=wait)
    assert bound.decision_port() is decide and bound.approval_waiter() is wait

    good = plan_of(
        [
            branch("b", "exclusive", "x", "y"),
            tool("x", "b"),
            tool("y", "b"),
            join("j", "b", "x", "y"),
        ],
        "j",
    )
    validate_dependency_plan(
        good, tool_caller=Fakes().caller, decision_port=decide, approval_waiter=wait
    )
    bad = {
        "unknown branch field": RunTask(
            "b", branch={"kind": "parallel", "arms": [], "x": 1}
        ),
        "arm not a dependent": branch("b", "exclusive", "elsewhere"),
        "unknown kind": RunTask(
            "b", branch={"kind": "loop", "arms": [{"target_task_id": "x"}]}
        ),
        "two control kinds": RunTask(
            "b",
            join={"branch_task_id": "b"},
            approval={"approver_role": "o", "timeout_s": 1},
        ),
        "a call and a control kind": RunTask(
            "b", call=ToolStep("s", "t", {}), approval={"approver_role": "o"}
        ),
        "approval without role": RunTask("b", approval={"timeout_s": 1}),
        "negative timeout": RunTask(
            "b", approval={"approver_role": "o", "timeout_s": -1}
        ),
        "decision without iri": RunTask("b", decision={"inputs": []}),
    }
    for label, task in bad.items():
        with pytest.raises(ValueError):
            validate_dependency_plan(
                plan_of([task, tool("x", "b")], "x"),
                tool_caller=Fakes().caller,
                decision_port=decide,
                approval_waiter=wait,
            )
        assert label


@pytest.mark.spec(SPEC_2)
async def test_exclusive_branch_skips_untaken_arm_until_join(events):
    fakes = Fakes(
        verdicts={"b": DecisionVerdict(record_id="rec-1", taken_task_ids=("x",))}
    )
    tasks = [
        tool("p"),
        branch("b", "exclusive", "x", "y", deps=("p",)),
        tool("x", "b"),
        tool("y", "b"),
        tool("y2", "y"),
        join("j", "b", "x", "y2"),
    ]
    result = await run(support.request(task="t"), plan=plan_of(tasks, "j"))
    assert result.status is RunStatus.COMPLETED, result.error
    assert fakes.called == ["p", "x"]
    question, payload, task_id = fakes.asked[0]
    assert (question, task_id) == ("BranchSelect", "b")
    assert payload["plan_digest"] == "sha256:plan"
    assert payload["branch_task_id"] == "b"
    assert list(payload["inputs"]) == ["p"]
    skipped = {e["task_id"]: e for e in kinds(events, "task_skipped")}
    assert set(skipped) == {"y", "y2"}
    assert all(e["not_taken_arm"] == "y" for e in skipped.values())
    assert not any(
        e["type"] == "task_started" and e["task_id"] in {"y", "y2"} for e in events
    )
    assert not kinds(events, "task_blocked")
    assert json.loads(result.output) == {"task": "x"} or "x" in result.output


@pytest.mark.spec(SPEC_2)
@pytest.mark.parametrize(
    ("kind", "taken", "expected_called"),
    [
        ("exclusive", ("zzz",), []),
        ("exclusive", ("x", "y"), []),
        ("exclusive", (), []),
        ("inclusive", ("x", "y"), ["x", "y"]),
    ],
)
async def test_branch_verdict_is_validated_against_declared_arms(
    kind, taken, expected_called, events
):
    fakes = Fakes(verdicts={"b": DecisionVerdict(taken_task_ids=taken)})
    tasks = [
        branch("b", kind, "x", "y"),
        tool("x", "b"),
        tool("y", "b"),
        join("j", "b", "x", "y"),
    ]
    result = await run(support.request(task="t"), plan=plan_of(tasks, "j"))
    assert fakes.called == expected_called
    if expected_called:
        assert result.status is RunStatus.COMPLETED
        assert not kinds(events, "task_skipped")
    else:
        assert result.status is RunStatus.FAILED
        assert "BranchVerdictInvalid" in (result.error or "")


@pytest.mark.spec(SPEC_2)
async def test_branch_abstention_fails_the_step(events):
    Fakes(verdicts={"b": DecisionVerdict(abstention="BRANCH_NO_ARM")})
    tasks = [branch("b", "exclusive", "x"), tool("x", "b"), join("j", "b", "x")]
    result = await run(support.request(task="t"), plan=plan_of(tasks, "j"))
    assert result.status is RunStatus.FAILED
    assert "BRANCH_NO_ARM" in (result.error or "")


@pytest.mark.spec(SPEC_2)
async def test_parallel_split_skips_the_decision_port(events):
    fakes = Fakes()
    tasks = [
        branch("b", "parallel", "x", "y"),
        tool("x", "b"),
        tool("y", "b"),
        join("j", "b", "x", "y"),
    ]
    result = await run(support.request(task="t"), plan=plan_of(tasks, "j"))
    assert result.status is RunStatus.COMPLETED, result.error
    assert fakes.asked == [] and sorted(fakes.called) == ["x", "y"]
    verdict = kinds(events, "branch_verdict")[0]
    assert verdict["taken_task_ids"] == ["x", "y"]


@pytest.mark.spec(SPEC_2)
async def test_join_with_every_arm_skipped_is_skipped_too(events):
    Fakes(verdicts={"outer": DecisionVerdict(taken_task_ids=("keep",))})
    tasks = [
        branch("outer", "exclusive", "keep", "side"),
        tool("keep", "outer"),
        tool("side", "outer"),
        branch("inner", "parallel", "i1", deps=("side",)),
        tool("i1", "inner"),
        join("inner_join", "inner", "i1"),
        join("j", "outer", "keep", "inner_join"),
    ]
    result = await run(support.request(task="t"), plan=plan_of(tasks, "j"))
    assert result.status is RunStatus.COMPLETED, result.error
    skipped = {e["task_id"] for e in kinds(events, "task_skipped")}
    assert skipped == {"side", "inner", "i1", "inner_join"}


@pytest.mark.spec(SPEC_2)
def test_arm_closure_keeps_only_joins():
    nodes = {
        t.task_id: t
        for t in [
            branch("b", "exclusive", "x", "y"),
            tool("x", "b"),
            tool("y", "b"),
            tool("y2", "y"),
            join("j", "b", "x", "y2"),
        ]
    }
    children = {"b": ["x", "y"], "x": ["j"], "y": ["y2"], "y2": ["j"], "j": []}
    skipped, edges = arm_closure("y", nodes, children)
    assert set(skipped) == {"y", "y2"} and edges == (("y2", "j"),)
    assert arm_closure("j", nodes, children) == ((), ())


@pytest.mark.spec(SPEC_2)
def test_skip_edge_admits_an_untaken_arm_guard():
    for status in (TaskStatus.BLOCKED, TaskStatus.READY):
        assert (
            transition_task(status, TaskEvent.SKIP, {"arm_not_taken": True})
            is TaskStatus.SKIPPED
        )
        with pytest.raises(TaskTransitionRefused):
            transition_task(status, TaskEvent.SKIP, {})


@pytest.mark.spec(SPEC_3)
async def test_approval_wait_frees_slot_and_reject_skips_dependents(events):
    gate = asyncio.Event()
    fakes = Fakes(gate=gate)
    tasks = [
        approval("ap"),
        tool("t1"),
        tool("t2"),
        tool("after", "ap"),
        tool("j", "after", "t1", "t2"),
    ]
    # capacity one: an approval that held its slot would starve t1 and t2
    parent = asyncio.create_task(
        run(support.request(task="t"), plan=plan_of(tasks, "j", capacity=1))
    )
    try:
        await fakes.until(lambda: {"t1", "t2"} <= set(fakes.called) and fakes.waits)
        assert {"t1", "t2"} <= set(fakes.called) and not parent.done()
        assert fakes.waits == [(fakes.waits[0][0], "ap", "owner", 30)]
        gate.set()
        result = await parent
    finally:
        gate.set()
        if not parent.done():
            parent.cancel()
        await asyncio.gather(parent, return_exceptions=True)
    assert result.status is RunStatus.COMPLETED, result.error
    done = next(e for e in kinds(events, "task_completed") if e["task_id"] == "ap")
    assert json.loads(done["output"]) == {
        "approver": "alice",
        "verdict": "approve",
        "decided_at_ms": 1234,
    }


@pytest.mark.spec(SPEC_3)
@pytest.mark.parametrize(
    ("verdict", "error"),
    [("reject", "ApprovalRejected"), ("timeout", "ApprovalTimedOut")],
)
async def test_rejected_or_timed_out_approval_fails_and_skips_dependents(
    verdict, error, events
):
    fakes = Fakes(outcome=ApprovalOutcome(verdict))
    tasks = [approval("ap"), tool("after", "ap")]
    result = await run(support.request(task="t"), plan=plan_of(tasks, "after"))
    assert result.status is RunStatus.FAILED
    assert error in (result.error or "")
    assert fakes.called == []
    assert any(e["type"] == "task_blocked" and e["task_id"] == "after" for e in events)


@pytest.mark.spec(SPEC_3)
async def test_cancelled_run_cancels_the_approval_wait(events):
    gate = asyncio.Event()
    fakes = Fakes(gate=gate)
    parent = asyncio.create_task(
        run(
            support.request(task="t"),
            plan=plan_of([approval("ap"), tool("a", "ap")], "a"),
        )
    )
    await fakes.until(lambda: fakes.waits)
    parent.cancel()
    await asyncio.gather(parent, return_exceptions=True)
    ended = kinds(events, "approval_wait_ended")
    assert ended and ended[0]["outcome"] == "cancelled"
    assert fakes.called == []


@pytest.mark.spec(SPEC_3)
async def test_missing_approval_timeout_uses_the_default():
    fakes = Fakes()
    tasks = [
        RunTask("ap", approval={"approver_role": "owner"}),
        tool("a", "ap"),
    ]
    result = await run(support.request(task="t"), plan=plan_of(tasks, "a"))
    assert result.status is RunStatus.COMPLETED, result.error
    assert fakes.waits[0][3] == 86_400


@pytest.mark.spec(SPEC_4)
async def test_decision_step_outputs_verdict_and_abstention_fails(events):
    fakes = Fakes(
        verdicts={"d": DecisionVerdict(outputs=[{"tier": "gold"}], record_id="rec-2")}
    )
    tasks = [
        tool("p", arguments={"unused": 1}),
        RunTask(
            "d",
            depends_on=("p",),
            decision={
                "decision_iri": "urn:dmn:discount",
                "inputs": ["steps.p.output.task"],
            },
        ),
        tool("use", "d", arguments={"tier": {"$ref": "steps.d.output.0.tier"}}),
    ]
    result = await run(support.request(task="t"), plan=plan_of(tasks, "use"))
    assert result.status is RunStatus.COMPLETED, result.error
    question, payload, _ = fakes.asked[0]
    assert question == "DecisionTableEval"
    assert payload == {"decision_iri": "urn:dmn:discount", "inputs": {"task": "p"}}
    assert json.loads(result.output)["tier"] == "gold"

    fakes = Fakes(verdicts={"d": DecisionVerdict(abstention="DMN_NO_RULE_MATCHED")})
    result = await run(support.request(task="t"), plan=plan_of(tasks, "use"))
    assert result.status is RunStatus.FAILED
    assert "DMN_NO_RULE_MATCHED" in (result.error or "")
    assert len(fakes.asked) == 1  # never retried
    assert "use" not in fakes.called


@pytest.mark.spec(SPEC_5)
@pytest.mark.parametrize(
    ("task", "bound", "error"),
    [
        (branch("c", "exclusive", "x"), "decision", DecisionPortUnavailable),
        (
            RunTask("c", decision={"decision_iri": "urn:d", "inputs": []}),
            "decision",
            DecisionPortUnavailable,
        ),
        (approval("c"), "approval", ApprovalWaiterUnavailable),
    ],
)
async def test_missing_ports_refuse_before_any_task(task, bound, error, events):
    fakes = Fakes()
    setattr(support.PORTS, bound, None)
    tasks = [task, tool("x", "c")]
    result = await run(support.request(task="t"), plan=plan_of(tasks, "x"))
    assert result.status is RunStatus.FAILED
    assert error.__name__ in (result.error or "")
    assert fakes.called == []
    assert [event["type"] for event in events] == ["error"]


@pytest.mark.spec(SPEC_5)
async def test_ports_not_needed_by_a_plan_may_be_unbound():
    support.PORTS.caller = Fakes().caller
    support.PORTS.decision = None
    support.PORTS.approval = None
    tasks = [
        branch("b", "parallel", "x"),
        tool("x", "b"),
        join("j", "b", "x"),
    ]
    result = await run(support.request(task="t"), plan=plan_of(tasks, "j"))
    assert result.status is RunStatus.COMPLETED, result.error


@pytest.mark.spec(SPEC_6)
async def test_events_cite_verdicts_waits_and_skips(events):
    Fakes(verdicts={"b": DecisionVerdict(record_id="rec-9", taken_task_ids=("x",))})
    tasks = [
        branch("b", "exclusive", "x", "y"),
        tool("x", "b"),
        tool("y", "b"),
        approval("ap", "x"),
        join("j", "b", "ap", "y"),
    ]
    result = await run(support.request(task="t"), plan=plan_of(tasks, "j"))
    assert result.status is RunStatus.COMPLETED, result.error
    verdict = kinds(events, "branch_verdict")
    assert [(e["task_id"], e["taken_task_ids"], e["record_id"]) for e in verdict] == [
        ("b", ["x"], "rec-9")
    ]
    started = kinds(events, "approval_wait_started")
    ended = kinds(events, "approval_wait_ended")
    assert [e["task_id"] for e in started] == ["ap"]
    assert started[0]["approver_role"] == "owner" and started[0]["timeout_s"] == 30
    assert [e["outcome"] for e in ended] == ["approve"]
    order = [e["type"] for e in events if e.get("task_id") == "ap"]
    assert order.index("approval_wait_started") < order.index("approval_wait_ended")
    skipped = kinds(events, "task_skipped")
    assert [
        (e["task_id"], e["not_taken_arm"], e["branch_task_id"]) for e in skipped
    ] == [("y", "y", "b")]


@pytest.mark.spec(SPEC_7)
@pytest.mark.parametrize(
    ("outcome", "status", "error"),
    [
        (ApprovalOutcome("approve", "alice", 7), RunStatus.COMPLETED, None),
        (ApprovalOutcome("reject"), RunStatus.FAILED, "ApprovalRejected"),
        (ApprovalOutcome("timeout"), RunStatus.FAILED, "ApprovalTimedOut"),
    ],
)
async def test_process_plan_branches_waits_and_joins(outcome, status, error, events):
    fakes = Fakes(
        verdicts={
            "route": DecisionVerdict(record_id="r1", taken_task_ids=("fast",)),
            "rule": DecisionVerdict(outputs={"limit": 5}),
        },
        outcome=outcome,
    )
    tasks = [
        tool("intake"),
        branch("route", "exclusive", "fast", "slow", deps=("intake",)),
        tool("fast", "route"),
        tool("slow", "route"),
        join("merge", "route", "fast", "slow"),
        branch("split", "parallel", "left", "right", deps=("merge",)),
        tool("left", "split"),
        tool("right", "split"),
        join("both", "split", "left", "right"),
        RunTask(
            "rule",
            depends_on=("both",),
            decision={"decision_iri": "urn:dmn:limit", "inputs": []},
        ),
        approval("sign", "rule"),
        tool("done", "sign"),
    ]
    result = await run(support.request(task="t"), plan=plan_of(tasks, "done"))
    assert result.status is status, result.error
    assert [q for q, _, _ in fakes.asked] == ["BranchSelect", "DecisionTableEval"]
    assert "slow" not in fakes.called
    assert fakes.called[:2] == ["intake", "fast"]
    assert set(fakes.called[2:4]) == {"left", "right"}
    assert [e["task_id"] for e in kinds(events, "task_skipped")] == ["slow"]
    assert fakes.waits and fakes.waits[0][1] == "sign"
    if error is None:
        assert fakes.called[-1] == "done"
        assert json.loads(result.output)["task"] == "done"
    else:
        assert error in (result.error or "")
        assert "done" not in fakes.called


@pytest.mark.spec(SPEC_2)
async def test_tool_finished_behind_a_skipped_arm_tool_is_released(events):
    gate = asyncio.Event()
    fakes = Fakes(
        verdicts={"b": DecisionVerdict(taken_task_ids=("x",))}, decide_gate=gate
    )
    tasks = [
        branch("b", "exclusive", "x", "y"),
        tool("x", "b"),
        tool("y", "b"),
        tool("z"),
        join("j", "b", "x", "y"),
        tool("end", "j", "z"),
    ]
    parent = asyncio.create_task(
        run(support.request(task="t"), plan=plan_of(tasks, "end"))
    )
    try:
        await fakes.until(lambda: "z" in fakes.called and fakes.asked)
        gate.set()
        result = await parent
    finally:
        gate.set()
        if not parent.done():
            parent.cancel()
        await asyncio.gather(parent, return_exceptions=True)
    assert result.status is RunStatus.COMPLETED, result.error
    assert fakes.called[-1] == "end" and "y" not in fakes.called


class _Checkpoint:
    admission_identity = "admitted-work-item/run"

    def __init__(self):
        self.content = None
        self.snapshots = []

    async def restore(self):
        return self.content

    async def save(self, content):
        self.content = content
        self.snapshots.append(content)


@pytest.mark.spec(SPEC_2)
async def test_restored_branch_reapplies_its_recorded_verdict(events):
    store = _Checkpoint()

    @contextlib.asynccontextmanager
    async def scope(request, plan):
        yield store

    support.PORTS.run_checkpoints = None
    support.PORTS.checkpoint_store = scope
    fakes = Fakes(verdicts={"b": DecisionVerdict(taken_task_ids=("x",))})
    tasks = [
        branch("b", "exclusive", "x", "y"),
        tool("x", "b"),
        tool("y", "b"),
        join("j", "b", "x", "y"),
    ]
    plan = plan_of(tasks, "j")
    request = support.request(task="t", run_id="branch-run")
    stream = iterate_run(request, plan=plan)
    async for event in stream:
        if event["type"] == "task_completed" and event["task_id"] == "b":
            break
    await stream.aclose()
    store.content = next(
        content
        for content in reversed(store.snapshots)
        if json.loads(content)["task_states"] == {"b": "done"}
    )
    fakes.called.clear()
    fakes.asked.clear()
    result = await run(request, plan=plan)
    assert result.status is RunStatus.COMPLETED, result.error
    assert fakes.asked == [] and fakes.called == ["x"]
