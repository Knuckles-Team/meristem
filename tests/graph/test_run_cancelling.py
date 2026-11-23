"""Cancellation runs the machine: RE8, RE9 and the drain error of RE10.

A host cancel moves Running to Cancelling (RE8); the finished drain moves
Cancelling to Stopped(cancelled) (RE9); a drain that errors is a run-level
failure and moves Cancelling to Failed (RE10).
"""

from __future__ import annotations

import asyncio

import ports_support as support
import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

import meristem.graph.dependency as dependency_module
import meristem.graph.executor as executor_module
from meristem.graph import (
    DependencyPlan,
    RunPlan,
    RunStatus,
    RunTask,
    ToolStep,
    run,
)
from meristem.graph.state import (
    RunEvent,
    RunTransitionRefused,
    transition,
)

SPEC_1 = "MERISTEM-HARNESS-CYCLE-R086.1"
SPEC_2 = "MERISTEM-HARNESS-CYCLE-R086.2"


def _spy_transitions(monkeypatch: pytest.MonkeyPatch) -> list:
    """Every run and task state change of the run, in one ordered list.

    Entries are ``(kind, from, event, to)`` with values as strings. The
    serial path records run transitions through executor.py and the
    dependency driver through dependency.py; both read the module-level
    name at call time, so one list sees every change.
    """
    calls: list[tuple[str, str, str, str]] = []
    original_run = executor_module.transition
    original_run_dep = dependency_module.transition
    original_task = dependency_module.transition_task

    def spy_run(status, event, guard_facts=None):
        result = original_run(status, event, guard_facts)
        calls.append(("run", status.value, event.value, result.value))
        return result

    def spy_run_dep(status, event, guard_facts=None):
        result = original_run_dep(status, event, guard_facts)
        calls.append(("run", status.value, event.value, result.value))
        return result

    def spy_task(status, event, guard_facts=None):
        result = original_task(status, event, guard_facts)
        calls.append(("task", status.value, event.value, result.value))
        return result

    monkeypatch.setattr(executor_module, "transition", spy_run)
    monkeypatch.setattr(dependency_module, "transition", spy_run_dep)
    monkeypatch.setattr(dependency_module, "transition_task", spy_task)
    return calls


def _run_calls(calls: list) -> list:
    return [entry[1:] for entry in calls if entry[0] == "run"]


def _task_calls(calls: list) -> list:
    return [entry[1:] for entry in calls if entry[0] == "task"]


def _hang(entered: asyncio.Event):
    async def hang(messages, info):
        entered.set()
        await asyncio.Event().wait()
        return ModelResponse(parts=[TextPart("done")])

    return hang


@pytest.mark.spec(SPEC_1)
async def test_cancel_moves_running_to_cancelling_then_stopped_cancelled(
    monkeypatch: pytest.MonkeyPatch, events: list[dict]
) -> None:
    calls = _spy_transitions(monkeypatch)
    entered = asyncio.Event()
    plan = RunPlan(model=FunctionModel(_hang(entered)), steps=("answer",))
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
    assert _run_calls(calls) == [
        ("pending", "start", "running"),
        ("running", "cancel", "cancelling"),
        ("cancelling", "drain_complete", "stopped_cancelled"),
    ]
    assert events[-1]["type"] == "run_cancelled"
    assert events[-1]["status"] == "stopped_cancelled"


@pytest.mark.spec(SPEC_1)
async def test_finish_during_cancelling_is_refused(
    monkeypatch: pytest.MonkeyPatch, events: list[dict]
) -> None:
    """A cancel that lands while the step runs is never turned into a finish."""
    calls = _spy_transitions(monkeypatch)
    entered = asyncio.Event()

    async def hang(messages, info):
        entered.set()
        await asyncio.Event().wait()
        return ModelResponse(parts=[TextPart("STOP now")])

    plan = RunPlan(model=FunctionModel(hang), steps=("answer",))
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
    assert ("running", "finish", "completed") not in _run_calls(calls)
    assert events[-1]["status"] == "stopped_cancelled"
    # The machine itself refuses the pair.
    with pytest.raises(RunTransitionRefused) as caught:
        transition(
            RunStatus.CANCELLING, RunEvent.FINISH, {"output_task_complete": True}
        )
    assert caught.value.reason == "IllegalTransition"


@pytest.mark.spec(SPEC_1)
async def test_drain_error_moves_cancelling_to_failed(
    monkeypatch: pytest.MonkeyPatch, events: list[dict]
) -> None:
    """A drain that raises is a run-level failure, not a clean stop."""
    calls = _spy_transitions(monkeypatch)
    entered = asyncio.Event()

    async def caller(server, tool, arguments, task_id):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise RuntimeError("drain exploded") from None

    def model(messages, info):
        pytest.fail("drain-error plan must not call the model")

    support.register_tool_caller(caller)
    plan = RunPlan(
        model=FunctionModel(model),
        dependency_plan=DependencyPlan(
            tasks=(RunTask("tool", call=ToolStep("server", "wait", {})),),
            output_task_id="tool",
            plan_id="drain-error",
        ),
    )
    execution = asyncio.create_task(run(support.request(task="drain"), plan=plan))
    try:
        await asyncio.wait_for(entered.wait(), 0.5)
        execution.cancel("host stop")
        result = await asyncio.wait_for(execution, 5.0)
    finally:
        if not execution.done():
            execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)
    assert result.status is RunStatus.FAILED
    # The caller exception is host-wrapped on its way to the drain; the
    # proof of RE10 is that the run fails instead of stopping cleanly.
    assert "ToolCallFailed" in (result.error or "")
    assert _run_calls(calls) == [
        ("pending", "start", "running"),
        ("running", "cancel", "cancelling"),
        ("cancelling", "fail", "failed"),
    ]
    assert ("active", "fail", "failed") in _task_calls(calls)
    assert events[-1]["type"] == "error"


@pytest.mark.spec(SPEC_2)
async def test_cancel_cancels_active_tasks_and_skips_pending_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _spy_transitions(monkeypatch)
    entered = asyncio.Event()
    plan = RunPlan(
        model=FunctionModel(_hang(entered)),
        dependency_plan=DependencyPlan(
            tasks=(
                RunTask("p", "prompt p"),
                RunTask("c", "prompt c", ("p",)),
                RunTask("g", "prompt g", ("c",)),
            ),
            output_task_id="g",
            max_concurrency=1,
            plan_id="cancel-scheduler",
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
    task_calls = _task_calls(calls)
    assert ("active", "cancel", "cancelled") in task_calls
    assert task_calls.count(("blocked", "skip", "skipped")) == 2


@pytest.mark.spec(SPEC_2)
async def test_drain_complete_waits_for_the_last_active_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _spy_transitions(monkeypatch)
    both_active = asyncio.Event()
    model_calls = 0

    async def hang(messages, info):
        nonlocal model_calls
        model_calls += 1
        if model_calls == 2:
            both_active.set()
        await asyncio.Event().wait()
        return ModelResponse(parts=[TextPart("done")])

    plan = RunPlan(
        model=FunctionModel(hang),
        dependency_plan=DependencyPlan(
            tasks=(
                RunTask("a", "prompt a"),
                RunTask("b", "prompt b"),
                RunTask("join", "join", ("a", "b")),
            ),
            output_task_id="join",
            max_concurrency=2,
            plan_id="drain-wait",
        ),
    )
    execution = asyncio.create_task(run(support.request(task="drain"), plan=plan))
    try:
        await asyncio.wait_for(both_active.wait(), 0.5)
        execution.cancel("host stop")
        with pytest.raises(asyncio.CancelledError):
            await execution
    finally:
        if not execution.done():
            execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)
    cancellations = [
        index
        for index, call in enumerate(calls)
        if call == ("task", "active", "cancel", "cancelled")
    ]
    drain = [
        index
        for index, call in enumerate(calls)
        if call == ("run", "cancelling", "drain_complete", "stopped_cancelled")
    ]
    assert len(cancellations) == 2
    assert len(drain) == 1
    # The run only drains after every active task has cancelled.
    assert drain[0] > max(cancellations)
    assert calls[-1] == (
        "run",
        "cancelling",
        "drain_complete",
        "stopped_cancelled",
    )
