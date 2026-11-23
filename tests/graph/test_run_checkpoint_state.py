"""Checkpoint restore records the run state and task states exactly.

R084.4: the persisted field is ``run_status``; restore reads it back and
never infers a terminal state for a run that was still running. R085.2: the
persisted field per task is ``task_states``; a stored value outside the
closed enum refuses restore with ``RUN_STATE_UNKNOWN``.
"""

from __future__ import annotations

import json

import pytest
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from meristem.graph.checkpoint import (
    RUN_STATE_UNKNOWN,
    CheckpointUnavailable,
    RunCheckpoint,
)
from meristem.graph.state import (
    DependencyPlan,
    GraphState,
    RunPlan,
    RunStatus,
    RunTask,
    StopRule,
    TaskStatus,
)

SPEC_RUN = "MERISTEM-HARNESS-CYCLE-R084.4"
SPEC_TASK = "MERISTEM-HARNESS-CYCLE-R085.2"

RUN_ID = "run-1"
ADMISSION = "admitted-work-item/run-1"

#: One codec-valid message history, in the JSON shape the snapshot codec
#: round-trips (the same dump the snapshot save performs).
MESSAGES = ModelMessagesTypeAdapter.dump_python(
    [
        ModelRequest(parts=[UserPromptPart("first")]),
        ModelResponse(parts=[TextPart("a")]),
    ],
    mode="json",
)


class StoredPort:
    """A host checkpoint transaction that only restores, never saves."""

    admission_identity = ADMISSION

    def __init__(self, content: str | None) -> None:
        self.content = content

    async def restore(self) -> str | None:
        return self.content

    async def save(self, content: str) -> None:
        raise AssertionError("restore under test must not save")


def _serial_plan() -> RunPlan:
    return RunPlan(
        model="model-x",
        steps=("first", "second"),
        stop=StopRule(stop_markers=("STOP",)),
    )


async def _restore_serial(payload: dict) -> RunCheckpoint:
    plan = _serial_plan()
    state = GraphState(run_id=RUN_ID, query="t")
    port = StoredPort(None)
    checkpoint = RunCheckpoint(port, state, plan)
    payload = dict(payload)
    payload.setdefault("schema_version", 1)
    payload["run_id"] = RUN_ID
    payload["admission_identity"] = ADMISSION
    payload["plan_digest"] = checkpoint.digest
    payload.setdefault("messages", MESSAGES)
    payload.setdefault("task_messages", {})
    payload.setdefault("dependency_outputs", {})
    payload.setdefault(
        "usage",
        {
            "requests": 1,
            "tool_calls": 0,
            "input_tokens": 5,
            "output_tokens": 3,
            "total_tokens": 8,
        },
    )
    payload.setdefault("elapsed_seconds", 0.5)
    port.content = json.dumps(payload)
    await checkpoint.restore()
    return checkpoint


# spec: MERISTEM-HARNESS-CYCLE-R084.4
@pytest.mark.spec(SPEC_RUN)
async def test_restore_keeps_failed() -> None:
    checkpoint = await _restore_serial(
        {
            "phase": "completed",
            "task_states": {"step:0": "done", "step:1": "done"},
            "next_step": 2,
            "outputs": ["a", "b"],
            "run_status": "failed",
        }
    )
    assert checkpoint.state.status is RunStatus.FAILED


@pytest.mark.spec(SPEC_RUN)
@pytest.mark.parametrize(
    ("stored", "expected_reason"),
    [
        ("stopped_requested", "requested"),
        ("stopped_budget", "budget"),
        ("stopped_cancelled", "cancelled"),
    ],
)
async def test_restore_keeps_each_stop_reason(
    stored: str, expected_reason: str
) -> None:
    checkpoint = await _restore_serial(
        {
            "phase": "completed",
            "task_states": {"step:0": "done"},
            "next_step": 1,
            "outputs": ["STOP"],
            "run_status": stored,
        }
    )
    assert checkpoint.state.status.value == stored
    assert checkpoint.state.status.stop_reason == expected_reason


@pytest.mark.spec(SPEC_RUN)
async def test_restore_never_infers_completed_from_running() -> None:
    # Every step is done, yet the stored run state is still Running: restore
    # must keep it exactly, without filling in a terminal state.
    checkpoint = await _restore_serial(
        {
            "phase": "completed",
            "task_states": {"step:0": "done", "step:1": "done"},
            "next_step": 2,
            "outputs": ["a", "b"],
            "run_status": "running",
        }
    )
    assert checkpoint.state.status is RunStatus.RUNNING


@pytest.mark.spec(SPEC_RUN)
async def test_restore_refuses_an_unknown_value_with_run_state_unknown() -> None:
    with pytest.raises(CheckpointUnavailable) as caught:
        await _restore_serial(
            {
                "phase": "completed",
                "task_states": {"step:0": "done"},
                "next_step": 1,
                "outputs": ["a"],
                "run_status": "paused",
            }
        )
    assert caught.value.code == RUN_STATE_UNKNOWN


def _dependency_plan() -> RunPlan:
    return RunPlan(
        model="model-x",
        dependency_plan=DependencyPlan(
            tasks=(
                RunTask("a", "prompt a"),
                RunTask("b", "prompt b"),
                RunTask("c", "prompt c", ("a", "b")),
                RunTask("d", "prompt d", ("c",)),
            ),
            output_task_id="c",
            max_concurrency=2,
            plan_id="census-plan",
        ),
    )


async def _restore_dependency(payload: dict) -> RunCheckpoint:
    plan = _dependency_plan()
    state = GraphState(run_id=RUN_ID, query="t")
    port = StoredPort(None)
    checkpoint = RunCheckpoint(port, state, plan)
    payload = dict(payload)
    payload.setdefault("schema_version", 1)
    payload["run_id"] = RUN_ID
    payload["admission_identity"] = ADMISSION
    payload["plan_digest"] = checkpoint.digest
    payload.setdefault("messages", MESSAGES)
    payload.setdefault("task_messages", {})
    payload.setdefault(
        "usage",
        {
            "requests": 1,
            "tool_calls": 0,
            "input_tokens": 5,
            "output_tokens": 3,
            "total_tokens": 8,
        },
    )
    payload.setdefault("elapsed_seconds", 0.5)
    port.content = json.dumps(payload)
    await checkpoint.restore()
    return checkpoint


@pytest.mark.spec(SPEC_TASK)
async def test_restore_keeps_every_task_status() -> None:
    checkpoint = await _restore_dependency(
        {
            "phase": "completed",
            "task_states": {
                "a": "done",
                "b": "done",
                "c": "ready",
                "d": "blocked",
            },
            "next_step": 2,
            "outputs": [],
            "dependency_outputs": {"a": "A", "b": "B"},
            "run_status": "running",
        }
    )
    assert checkpoint.task_states == {
        "a": TaskStatus.DONE,
        "b": TaskStatus.DONE,
        "c": TaskStatus.READY,
        "d": TaskStatus.BLOCKED,
    }
    # The pending, active and completed collections are views of the map.
    assert checkpoint.completed_tasks == {"a", "b"}
    assert checkpoint.inflight_tasks == set()


@pytest.mark.spec(SPEC_TASK)
async def test_restore_refuses_an_unknown_task_status() -> None:
    with pytest.raises(CheckpointUnavailable) as caught:
        await _restore_dependency(
            {
                "phase": "completed",
                "task_states": {
                    "a": "exploding",
                    "b": "done",
                    "c": "ready",
                    "d": "blocked",
                },
                "next_step": 2,
                "outputs": [],
                "dependency_outputs": {"b": "B"},
                "run_status": "running",
            }
        )
    assert caught.value.code == RUN_STATE_UNKNOWN
