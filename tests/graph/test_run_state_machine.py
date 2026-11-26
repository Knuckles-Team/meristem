"""The closed run state machine: the states, the edges, the refusals.

``transition`` in ``meristem/graph/state.py`` is the only writer of the run
status; this file proves the table RE1 to RE10, the closed refusal set, the
work-item vocabulary map and that the wiring carries no direct write.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from meristem.graph.state import (
    RUN_TERMINAL_VOCAB,
    RunEvent,
    RunStatus,
    RunTransitionRefused,
    transition,
)

SPEC_1 = "MERISTEM-HARNESS-CYCLE-R084.1"
SPEC_2 = "MERISTEM-HARNESS-CYCLE-R084.2"
SPEC_3 = "MERISTEM-HARNESS-CYCLE-R084.3"

RE1 = (RunStatus.PENDING, RunEvent.START, {"plan_received": True}, RunStatus.RUNNING)
RE2 = (RunStatus.PENDING, RunEvent.FAIL, None, RunStatus.FAILED)
RE3 = (RunStatus.PENDING, RunEvent.CANCEL, None, RunStatus.STOPPED_CANCELLED)
RE4 = (
    RunStatus.RUNNING,
    RunEvent.FINISH,
    {"output_task_complete": True},
    RunStatus.COMPLETED,
)
RE5 = (RunStatus.RUNNING, RunEvent.STOP_REQUESTED, None, RunStatus.STOPPED_REQUESTED)
RE6 = (RunStatus.RUNNING, RunEvent.STOP_BUDGET, None, RunStatus.STOPPED_BUDGET)
RE7 = (RunStatus.RUNNING, RunEvent.FAIL, None, RunStatus.FAILED)
RE8 = (RunStatus.RUNNING, RunEvent.CANCEL, None, RunStatus.CANCELLING)
RE9 = (
    RunStatus.CANCELLING,
    RunEvent.DRAIN_COMPLETE,
    {"no_active_task": True},
    RunStatus.STOPPED_CANCELLED,
)
RE10 = (RunStatus.CANCELLING, RunEvent.FAIL, None, RunStatus.FAILED)

LEGAL_RUN_EDGES = (
    ("RE1", *RE1),
    ("RE2", *RE2),
    ("RE3", *RE3),
    ("RE4", *RE4),
    ("RE5", *RE5),
    ("RE6", *RE6),
    ("RE7", *RE7),
    ("RE8", *RE8),
    ("RE9", *RE9),
    ("RE10", *RE10),
)

TERMINAL_STATES = (
    RunStatus.COMPLETED,
    RunStatus.FAILED,
    RunStatus.STOPPED_REQUESTED,
    RunStatus.STOPPED_BUDGET,
    RunStatus.STOPPED_CANCELLED,
)


# spec: MERISTEM-HARNESS-CYCLE-R084.1
@pytest.mark.spec(SPEC_1)
@pytest.mark.parametrize(
    ("name", "source", "event", "guards", "target"),
    LEGAL_RUN_EDGES,
    ids=[edge[0] for edge in LEGAL_RUN_EDGES],
)
def test_every_legal_run_edge_moves_as_specified(
    name: str,
    source: RunStatus,
    event: RunEvent,
    guards: dict[str, bool] | None,
    target: RunStatus,
) -> None:
    assert transition(source, event, guards) is target


@pytest.mark.spec(SPEC_1)
def test_pending_start_moves_to_running() -> None:
    assert transition(RunStatus.PENDING, RunEvent.START, {"plan_received": True}) is (
        RunStatus.RUNNING
    )


@pytest.mark.spec(SPEC_1)
def test_pending_fail_moves_to_failed() -> None:
    assert transition(RunStatus.PENDING, RunEvent.FAIL) is RunStatus.FAILED


@pytest.mark.spec(SPEC_1)
def test_pending_cancel_moves_to_stopped_cancelled() -> None:
    assert transition(RunStatus.PENDING, RunEvent.CANCEL) is RunStatus.STOPPED_CANCELLED


@pytest.mark.spec(SPEC_1)
def test_running_finish_moves_to_completed() -> None:
    assert (
        transition(RunStatus.RUNNING, RunEvent.FINISH, {"output_task_complete": True})
        is RunStatus.COMPLETED
    )


@pytest.mark.spec(SPEC_1)
def test_running_stop_requested_moves_to_stopped_requested() -> None:
    assert transition(RunStatus.RUNNING, RunEvent.STOP_REQUESTED) is (
        RunStatus.STOPPED_REQUESTED
    )


@pytest.mark.spec(SPEC_1)
def test_running_stop_budget_moves_to_stopped_budget() -> None:
    assert transition(RunStatus.RUNNING, RunEvent.STOP_BUDGET) is (
        RunStatus.STOPPED_BUDGET
    )


@pytest.mark.spec(SPEC_1)
def test_running_fail_moves_to_failed() -> None:
    assert transition(RunStatus.RUNNING, RunEvent.FAIL) is RunStatus.FAILED


@pytest.mark.spec(SPEC_1)
def test_running_cancel_moves_to_cancelling() -> None:
    assert transition(RunStatus.RUNNING, RunEvent.CANCEL) is RunStatus.CANCELLING


@pytest.mark.spec(SPEC_1)
def test_cancelling_drain_complete_moves_to_stopped_cancelled() -> None:
    assert (
        transition(
            RunStatus.CANCELLING, RunEvent.DRAIN_COMPLETE, {"no_active_task": True}
        )
        is RunStatus.STOPPED_CANCELLED
    )


@pytest.mark.spec(SPEC_1)
def test_cancelling_fail_moves_to_failed() -> None:
    assert transition(RunStatus.CANCELLING, RunEvent.FAIL) is RunStatus.FAILED


@pytest.mark.spec(SPEC_1)
def test_running_finish_without_output_is_refused_with_guard_failed() -> None:
    with pytest.raises(RunTransitionRefused) as caught:
        transition(RunStatus.RUNNING, RunEvent.FINISH, {})
    assert caught.value.reason == "GuardFailed"


@pytest.mark.spec(SPEC_1)
def test_cancelling_drain_complete_with_active_task_is_refused_with_guard_failed() -> (
    None
):
    with pytest.raises(RunTransitionRefused) as caught:
        transition(RunStatus.CANCELLING, RunEvent.DRAIN_COMPLETE, {})
    assert caught.value.reason == "GuardFailed"


@pytest.mark.spec(SPEC_1)
def test_pending_start_without_plan_is_refused_with_guard_failed() -> None:
    with pytest.raises(RunTransitionRefused) as caught:
        transition(RunStatus.PENDING, RunEvent.START, {})
    assert caught.value.reason == "GuardFailed"


@pytest.mark.spec(SPEC_1)
def test_terminal_map_targets_the_work_item_vocabulary() -> None:
    assert dict(RUN_TERMINAL_VOCAB) == {
        RunStatus.COMPLETED: "Succeeded",
        RunStatus.FAILED: "Failed",
        RunStatus.STOPPED_CANCELLED: "Cancelled",
        RunStatus.STOPPED_REQUESTED: "Cancelled",
        RunStatus.STOPPED_BUDGET: "Failed",
    }


@pytest.mark.spec(SPEC_1)
def test_requested_stop_maps_to_cancelled_and_budget_stop_to_failed() -> None:
    # A requested stop is a distinct terminal that keeps its reason and maps
    # to Cancelled, never to Succeeded.
    assert RUN_TERMINAL_VOCAB[RunStatus.STOPPED_REQUESTED] == "Cancelled"
    assert RunStatus.STOPPED_REQUESTED.stop_reason == "requested"
    assert RUN_TERMINAL_VOCAB[RunStatus.STOPPED_BUDGET] == "Failed"
    assert RunStatus.STOPPED_BUDGET.stop_reason == "budget"
    assert RunStatus.STOPPED_CANCELLED.stop_reason == "cancelled"


# spec: MERISTEM-HARNESS-CYCLE-R084.2
@pytest.mark.spec(SPEC_2)
def test_every_illegal_pair_is_refused_with_the_typed_error() -> None:
    legal = {
        (source, event)
        for source, event, _, _ in (RE1, RE2, RE3, RE4, RE5, RE6, RE7, RE8, RE9, RE10)
    }
    refused = 0
    for status in RunStatus:
        for event in RunEvent:
            if (status, event) in legal:
                continue
            refused += 1
            with pytest.raises(RunTransitionRefused, match="run transition refused"):
                transition(status, event, {})
    assert refused == len(RunStatus) * len(RunEvent) - len(legal)


@pytest.mark.spec(SPEC_2)
def test_terminal_states_refuse_every_event() -> None:
    assert TERMINAL_STATES == tuple(
        status for status in RunStatus if status.is_terminal
    )
    for status in TERMINAL_STATES:
        for event in RunEvent:
            with pytest.raises(RunTransitionRefused) as caught:
                transition(
                    status,
                    event,
                    {
                        "plan_received": True,
                        "output_task_complete": True,
                        "no_active_task": True,
                    },
                )
            assert caught.value.reason == "TerminalState"


@pytest.mark.spec(SPEC_2)
def test_pending_refuses_finish() -> None:
    with pytest.raises(RunTransitionRefused) as caught:
        transition(RunStatus.PENDING, RunEvent.FINISH, {"output_task_complete": True})
    assert caught.value.reason == "IllegalTransition"


@pytest.mark.spec(SPEC_2)
def test_cancelling_refuses_finish() -> None:
    with pytest.raises(RunTransitionRefused) as caught:
        transition(
            RunStatus.CANCELLING,
            RunEvent.FINISH,
            {"output_task_complete": True},
        )
    assert caught.value.reason == "IllegalTransition"


@pytest.mark.spec(SPEC_2)
def test_stopped_without_reason_is_not_constructible() -> None:
    # The closed enum has no bare Stopped member: every stopped state is a
    # distinct member that carries exactly one reason.
    with pytest.raises(ValueError):
        RunStatus("stopped")
    for status in RunStatus:
        if status.value.startswith("stopped"):
            assert status.stop_reason in ("requested", "budget", "cancelled")
    assert [
        status.value for status in RunStatus if status.value.startswith("stopped")
    ] == ["stopped_requested", "stopped_budget", "stopped_cancelled"]


@pytest.mark.spec(SPEC_2)
def test_refusal_carries_machine_from_event_and_reason() -> None:
    with pytest.raises(RunTransitionRefused) as caught:
        transition(RunStatus.PENDING, RunEvent.FINISH)
    refusal = caught.value
    assert refusal.machine == "run"
    assert refusal.code == "RUN_TRANSITION_REFUSED"
    assert refusal.from_status is RunStatus.PENDING
    assert refusal.event is RunEvent.FINISH
    assert refusal.reason == "IllegalTransition"


@pytest.mark.spec(SPEC_2)
def test_unknown_event_refuses_with_unknown_event() -> None:
    with pytest.raises(RunTransitionRefused) as caught:
        transition(RunStatus.RUNNING, "explode")
    assert caught.value.reason == "UnknownEvent"


# spec: MERISTEM-HARNESS-CYCLE-R084.3
@pytest.mark.spec(SPEC_3)
def test_no_module_writes_run_status_directly() -> None:
    """Grep proof: the wiring carries no status write outside transition().

    The only exact stored readback is the checkpoint restore, which records
    the run state as received (R084.4); it is not an inference.
    """
    root = Path(__file__).resolve().parents[2] / "meristem" / "graph"
    pattern = re.compile(r"(?m)^\s*(?:self\.)?state\.status\s*=\s*(\S.*)$")
    for module in ("executor.py", "dependency.py", "checkpoint.py"):
        source = (root / module).read_text(encoding="utf-8")
        writes = pattern.findall(source)
        assert writes, f"{module} must drive the run status through transition"
        for right in writes:
            allowed = right.startswith("transition(") or (
                module == "checkpoint.py" and right == "saved.run_status"
            )
            assert allowed, (
                f"direct run status write in {module}: state.status = {right}"
            )


def test_every_edge_and_refusal_of_the_run_machine() -> None:
    """Root proof of the rollup: the table and nothing else is admitted."""
    pytest.importorskip("meristem.graph.state")
    legal_pairs = {
        (source, event): (guards, target)
        for source, event, guards, target in (
            RE1,
            RE2,
            RE3,
            RE4,
            RE5,
            RE6,
            RE7,
            RE8,
            RE9,
            RE10,
        )
    }
    seen_legal: set[tuple[RunStatus, RunEvent]] = set()
    seen_refused = 0
    for status in RunStatus:
        for event in RunEvent:
            if (status, event) in legal_pairs:
                guards, target = legal_pairs[(status, event)]
                assert transition(status, event, guards) is target
                seen_legal.add((status, event))
            else:
                seen_refused += 1
                with pytest.raises(RunTransitionRefused):
                    transition(status, event, {})
    assert seen_legal == set(legal_pairs)
    assert seen_refused == len(RunStatus) * len(RunEvent) - len(legal_pairs)


# spec: MERISTEM-HARNESS-CYCLE-R084.3
@pytest.mark.spec(SPEC_3)
async def test_execute_graph_status_changes_all_pass_through_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hot-path proof: a driven run changes status only through transition()."""
    import ports_support as support
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import FunctionModel

    import meristem.graph.executor as executor_module
    from meristem.graph import RunPlan, run

    calls: list[tuple[RunStatus, str, RunStatus]] = []
    original = executor_module.transition

    def spy(status: RunStatus, event: RunEvent | str, guard_facts=None) -> RunStatus:
        result = original(status, event, guard_facts)
        calls.append((status, event if isinstance(event, str) else event.value, result))
        return result

    monkeypatch.setattr(executor_module, "transition", spy)

    def model(messages, info):
        return ModelResponse(parts=[TextPart(str(messages[-1].parts[-1].content))])

    plan = RunPlan(model=FunctionModel(model), steps=("first", "second"))
    result = await run(support.request(task="t"), plan=plan)

    assert result.status is RunStatus.COMPLETED
    assert calls == [
        (RunStatus.PENDING, "start", RunStatus.RUNNING),
        (RunStatus.RUNNING, "finish", RunStatus.COMPLETED),
    ]
