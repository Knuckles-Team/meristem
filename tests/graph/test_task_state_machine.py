"""The closed fan-out task machine: states, edges, refusals, scheduler wiring.

``transition_task`` in ``merstem/graph/dependency.py`` is the only writer of
a task status; this file proves the table TE1 to TE7, the closed refusal set
and the scheduler drives every task through it (failures skip descendants,
all failures are kept, a sibling completing after a failure is refused by
the guard, and no direct write remains).
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import ports_support as support
import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

import meristem.graph.dependency as dependency_module
from meristem.graph import (
    DependencyPlan,
    RunPlan,
    RunStatus,
    RunTask,
    ToolStep,
    run,
)
from meristem.graph.state import TaskEvent, TaskStatus, TaskTransitionRefused

transition_task = dependency_module.transition_task

SPEC_1 = "MERISTEM-HARNESS-CYCLE-R085.1"
SPEC_2 = "MERISTEM-HARNESS-CYCLE-R085.2"
SPEC_3 = "MERISTEM-HARNESS-CYCLE-R085.3"

TERMINAL_TASK_STATES = (
    TaskStatus.DONE,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
    TaskStatus.SKIPPED,
)

ALL_TASK_GUARDS = {
    "all_parents_done": True,
    "ancestor_failed_or_cancelled": True,
    "run_cancelling_or_failed": True,
    "arm_not_taken": True,
    "permit_held": True,
    "run_running": True,
}


# spec: MERISTEM-HARNESS-CYCLE-R085.1
@pytest.mark.spec(SPEC_1)
def test_blocked_deps_met_moves_to_ready() -> None:
    assert (
        transition_task(
            TaskStatus.BLOCKED, TaskEvent.DEPS_MET, {"all_parents_done": True}
        )
        is TaskStatus.READY
    )


@pytest.mark.spec(SPEC_1)
def test_blocked_skip_moves_to_skipped() -> None:
    assert (
        transition_task(
            TaskStatus.BLOCKED, TaskEvent.SKIP, {"ancestor_failed_or_cancelled": True}
        )
        is TaskStatus.SKIPPED
    )


@pytest.mark.spec(SPEC_1)
def test_ready_skip_moves_to_skipped() -> None:
    assert (
        transition_task(
            TaskStatus.READY, TaskEvent.SKIP, {"run_cancelling_or_failed": True}
        )
        is TaskStatus.SKIPPED
    )


@pytest.mark.spec(SPEC_1)
def test_ready_dispatch_moves_to_active() -> None:
    assert (
        transition_task(TaskStatus.READY, TaskEvent.DISPATCH, {"permit_held": True})
        is TaskStatus.ACTIVE
    )


@pytest.mark.spec(SPEC_1)
def test_active_complete_moves_to_done() -> None:
    assert (
        transition_task(TaskStatus.ACTIVE, TaskEvent.COMPLETE, {"run_running": True})
        is TaskStatus.DONE
    )


@pytest.mark.spec(SPEC_1)
def test_active_fail_moves_to_failed() -> None:
    assert transition_task(TaskStatus.ACTIVE, TaskEvent.FAIL) is TaskStatus.FAILED


@pytest.mark.spec(SPEC_1)
def test_active_cancel_moves_to_cancelled() -> None:
    assert transition_task(TaskStatus.ACTIVE, TaskEvent.CANCEL) is TaskStatus.CANCELLED


@pytest.mark.spec(SPEC_1)
def test_deps_met_with_an_unfinished_parent_is_refused_with_guard_failed() -> None:
    with pytest.raises(TaskTransitionRefused) as caught:
        transition_task(TaskStatus.BLOCKED, TaskEvent.DEPS_MET, {})
    assert caught.value.reason == "GuardFailed"


@pytest.mark.spec(SPEC_1)
def test_skip_without_a_failed_ancestor_is_refused_with_guard_failed() -> None:
    for source in (TaskStatus.BLOCKED, TaskStatus.READY):
        with pytest.raises(TaskTransitionRefused) as caught:
            transition_task(source, TaskEvent.SKIP, {})
        assert caught.value.reason == "GuardFailed"


def test_every_task_edge_and_refusal_of_the_task_machine() -> None:
    """Root proof of the rollup: the table and nothing else is admitted."""
    legal = {
        (TaskStatus.BLOCKED, TaskEvent.DEPS_MET): (
            {"all_parents_done": True},
            TaskStatus.READY,
        ),
        (TaskStatus.BLOCKED, TaskEvent.SKIP): (
            {"ancestor_failed_or_cancelled": True},
            TaskStatus.SKIPPED,
        ),
        (TaskStatus.READY, TaskEvent.SKIP): (
            {"ancestor_failed_or_cancelled": True},
            TaskStatus.SKIPPED,
        ),
        (TaskStatus.READY, TaskEvent.DISPATCH): (
            {"permit_held": True},
            TaskStatus.ACTIVE,
        ),
        (TaskStatus.ACTIVE, TaskEvent.COMPLETE): (
            {"run_running": True},
            TaskStatus.DONE,
        ),
        (TaskStatus.ACTIVE, TaskEvent.FAIL): (None, TaskStatus.FAILED),
        (TaskStatus.ACTIVE, TaskEvent.CANCEL): (None, TaskStatus.CANCELLED),
    }
    seen_legal = 0
    seen_refused = 0
    for status in TaskStatus:
        for event in TaskEvent:
            if (status, event) in legal:
                guards, target = legal[(status, event)]
                assert transition_task(status, event, guards) is target
                seen_legal += 1
            else:
                seen_refused += 1
                with pytest.raises(TaskTransitionRefused):
                    transition_task(status, event, ALL_TASK_GUARDS)
    assert seen_legal == len(legal)
    assert seen_refused == len(TaskStatus) * len(TaskEvent) - len(legal)


# spec: MERISTEM-HARNESS-CYCLE-R085.2
@pytest.mark.spec(SPEC_2)
def test_every_illegal_task_pair_is_refused_with_the_typed_error() -> None:
    legal = {
        (TaskStatus.BLOCKED, TaskEvent.DEPS_MET),
        (TaskStatus.BLOCKED, TaskEvent.SKIP),
        (TaskStatus.READY, TaskEvent.SKIP),
        (TaskStatus.READY, TaskEvent.DISPATCH),
        (TaskStatus.ACTIVE, TaskEvent.COMPLETE),
        (TaskStatus.ACTIVE, TaskEvent.FAIL),
        (TaskStatus.ACTIVE, TaskEvent.CANCEL),
    }
    refused = 0
    for status in TaskStatus:
        for event in TaskEvent:
            if (status, event) in legal:
                continue
            refused += 1
            with pytest.raises(TaskTransitionRefused, match="task transition refused"):
                transition_task(status, event, ALL_TASK_GUARDS)
    assert refused == len(TaskStatus) * len(TaskEvent) - len(legal)


@pytest.mark.spec(SPEC_2)
def test_terminal_task_refuses_every_event() -> None:
    assert [status for status in TaskStatus if status.is_terminal] == list(
        TERMINAL_TASK_STATES
    )
    for status in TERMINAL_TASK_STATES:
        for event in TaskEvent:
            with pytest.raises(TaskTransitionRefused) as caught:
                transition_task(status, event, ALL_TASK_GUARDS)
            assert caught.value.reason == "TerminalState"


@pytest.mark.spec(SPEC_2)
def test_task_refusal_carries_machine_from_event_and_reason() -> None:
    with pytest.raises(TaskTransitionRefused) as caught:
        transition_task(TaskStatus.BLOCKED, TaskEvent.COMPLETE, {"run_running": True})
    refusal = caught.value
    assert refusal.machine == "task"
    assert refusal.code == "TASK_TRANSITION_REFUSED"
    assert refusal.from_status is TaskStatus.BLOCKED
    assert refusal.event is TaskEvent.COMPLETE
    assert refusal.reason == "IllegalTransition"


# spec: MERISTEM-HARNESS-CYCLE-R085.3
@pytest.mark.spec(SPEC_3)
async def test_failed_task_skips_every_descendant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[TaskStatus, str, TaskStatus]] = []
    original = dependency_module.transition_task

    def spy(status: TaskStatus, event: TaskEvent | str, guard_facts=None) -> TaskStatus:
        result = original(status, event, guard_facts)
        calls.append((status, event if isinstance(event, str) else event.value, result))
        return result

    monkeypatch.setattr(dependency_module, "transition_task", spy)

    async def boom(messages, info):
        raise RuntimeError("parent blew up")

    plan = RunPlan(
        model=FunctionModel(boom),
        dependency_plan=DependencyPlan(
            tasks=(
                RunTask("p", "prompt p"),
                RunTask("c", "prompt c", ("p",)),
                RunTask("g", "prompt g", ("c",)),
            ),
            output_task_id="g",
            max_concurrency=1,
            plan_id="fail-plan",
        ),
    )
    result = await run(support.request(task="fail"), plan=plan)
    assert result.status is RunStatus.FAILED
    assert (TaskStatus.ACTIVE, "fail", TaskStatus.FAILED) in calls
    assert calls.count((TaskStatus.BLOCKED, "skip", TaskStatus.SKIPPED)) == 2


@pytest.mark.spec(SPEC_3)
async def test_cancelled_task_skips_every_descendant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[TaskStatus, str, TaskStatus]] = []
    original = dependency_module.transition_task

    def spy(status: TaskStatus, event: TaskEvent | str, guard_facts=None) -> TaskStatus:
        result = original(status, event, guard_facts)
        calls.append((status, event if isinstance(event, str) else event.value, result))
        return result

    monkeypatch.setattr(dependency_module, "transition_task", spy)

    entered = asyncio.Event()

    async def hang(messages, info):
        entered.set()
        await asyncio.Event().wait()
        return ModelResponse(parts=[TextPart("done")])

    plan = RunPlan(
        model=FunctionModel(hang),
        dependency_plan=DependencyPlan(
            tasks=(
                RunTask("p", "prompt p"),
                RunTask("c", "prompt c", ("p",)),
                RunTask("g", "prompt g", ("c",)),
            ),
            output_task_id="g",
            max_concurrency=1,
            plan_id="cancel-plan",
        ),
    )
    execution = asyncio.create_task(run(support.request(task="cancel"), plan=plan))
    try:
        await asyncio.wait_for(entered.wait(), 0.5)
        execution.cancel("host stop")
        with pytest.raises(asyncio.CancelledError):
            await execution
    finally:
        if not execution.done():
            execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)
    assert (TaskStatus.ACTIVE, "cancel", TaskStatus.CANCELLED) in calls
    assert calls.count((TaskStatus.BLOCKED, "skip", TaskStatus.SKIPPED)) == 2


@pytest.mark.spec(SPEC_3)
async def test_all_failures_are_kept() -> None:
    """The scheduler keeps every failure, not only the first it raises."""
    from meristem.graph import ToolCallFailed
    from meristem.graph.budget import SharedBudget
    from meristem.graph.dependency import iterate_dependency_plan
    from meristem.graph.state import GraphState, RunBudget

    async def caller(server, tool, arguments, task_id):
        raise RuntimeError(f"{tool} failed")

    def model(messages, info):
        pytest.fail("all-failure plan must not call the model")

    plan = RunPlan(
        model=FunctionModel(model),
        dependency_plan=DependencyPlan(
            tasks=(
                RunTask("a", call=ToolStep("server", "fail-a", {})),
                RunTask("b", call=ToolStep("server", "fail-b", {})),
                RunTask("join", "join", ("a", "b")),
            ),
            output_task_id="join",
            max_concurrency=2,
            plan_id="all-failures",
        ),
    )
    state = GraphState(run_id="all-failures", query="t")
    events: list[dict] = []

    def emit(state, kind, **fields):
        event = {"type": kind, **fields}
        events.append(event)
        return event

    raised: RuntimeError | None = None
    try:
        async for _event in iterate_dependency_plan(
            agent=object(),
            plan=plan,
            state=state,
            config={},
            attachments=[],
            budget=SharedBudget(RunBudget(request_limit=50)),
            emit=emit,
            deadline=None,
            tool_caller=caller,
        ):
            pass
    except RuntimeError as exc:
        raised = exc
    assert raised is not None
    assert len(raised.all_failures) == 2
    # The host-bound caller failure is wrapped; the original errors are kept
    # on the cause of every kept failure.
    assert all(isinstance(failure, ToolCallFailed) for failure in raised.all_failures)
    assert {str(failure.__cause__) for failure in raised.all_failures} == {
        "fail-a failed",
        "fail-b failed",
    }
    assert sum(event["type"] == "task_failed" for event in events) == 2


@pytest.mark.spec(SPEC_3)
async def test_no_sibling_completes_after_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sibling finishing after a failure is refused by the guard; discarded."""
    calls: list[tuple[TaskStatus, str, TaskStatus]] = []
    original = dependency_module.transition_task

    def spy(status: TaskStatus, event: TaskEvent | str, guard_facts=None) -> TaskStatus:
        result = original(status, event, guard_facts)
        calls.append((status, event if isinstance(event, str) else event.value, result))
        return result

    monkeypatch.setattr(dependency_module, "transition_task", spy)

    async def model(messages, info):
        prompt = str(messages[-1].parts[-1].content)
        if "fail" in prompt:
            raise RuntimeError("a failed")
        return ModelResponse(parts=[TextPart("sibling-done")])

    plan = RunPlan(
        model=FunctionModel(model),
        dependency_plan=DependencyPlan(
            tasks=(
                RunTask("a", "prompt fail"),
                RunTask("b", "prompt ok"),
                RunTask("join", "join", ("a", "b")),
            ),
            output_task_id="join",
            max_concurrency=2,
            plan_id="sibling-failure",
        ),
    )
    result = await run(support.request(task="t"), plan=plan)
    assert result.status is RunStatus.FAILED
    # The run fails with the task failure, not the transition refusal.
    assert "a failed" in (result.error or "")
    assert "refused" not in (result.error or "")
    assert (TaskStatus.ACTIVE, "fail", TaskStatus.FAILED) in calls
    # The sibling's complete is refused by the guard and its output discarded.
    assert (TaskStatus.ACTIVE, "complete", TaskStatus.DONE) not in calls
    assert result.output == ""


@pytest.mark.spec(SPEC_3)
def test_no_module_writes_task_status_directly() -> None:
    """Grep proof: task status moves only through transition_task().

    The only exact stored readback is the checkpoint restore seeding, which
    records the task states as received (R085.2).
    """
    root = Path(__file__).resolve().parents[2] / "meristem" / "graph"
    pattern = re.compile(r"(?m)^\s*task_state\[[^\]]+\]\s*=\s*(\S.*)$")
    dependency_source = (root / "dependency.py").read_text(encoding="utf-8")
    writes = pattern.findall(dependency_source)
    assert writes, "dependency.py must drive task status through transition_task"
    for module in ("dependency.py", "checkpoint.py"):
        source = (root / module).read_text(encoding="utf-8")
        for right in pattern.findall(source):
            allowed = right.startswith("transition_task(") or right == "stored"
            assert allowed, (
                f"direct task status write in {module}: task_state[...] = {right}"
            )


@pytest.mark.spec(SPEC_3)
async def test_run_dependency_graph_status_changes_all_pass_through_transition_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hot-path proof: a driven graph changes task status only via transition_task."""
    calls: list[tuple[TaskStatus, str, TaskStatus]] = []
    original = dependency_module.transition_task

    def spy(status: TaskStatus, event: TaskEvent | str, guard_facts=None) -> TaskStatus:
        result = original(status, event, guard_facts)
        calls.append((status, event if isinstance(event, str) else event.value, result))
        return result

    monkeypatch.setattr(dependency_module, "transition_task", spy)

    def model(messages, info):
        return ModelResponse(parts=[TextPart(str(messages[-1].parts[-1].content))])

    plan = RunPlan(
        model=FunctionModel(model),
        dependency_plan=DependencyPlan(
            tasks=(
                RunTask("a", "a"),
                RunTask("b", "b", ("a",)),
            ),
            output_task_id="b",
            max_concurrency=1,
            plan_id="hot-path",
        ),
    )
    result = await run(support.request(task="t"), plan=plan)
    assert result.status is RunStatus.COMPLETED
    assert calls == [
        (TaskStatus.BLOCKED, "deps_met", TaskStatus.READY),
        (TaskStatus.READY, "dispatch", TaskStatus.ACTIVE),
        (TaskStatus.ACTIVE, "complete", TaskStatus.DONE),
        (TaskStatus.BLOCKED, "deps_met", TaskStatus.READY),
        (TaskStatus.READY, "dispatch", TaskStatus.ACTIVE),
        (TaskStatus.ACTIVE, "complete", TaskStatus.DONE),
    ]
