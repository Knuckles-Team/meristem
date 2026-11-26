"""Apply a received dependency DAG with at most its declared active task count."""

from __future__ import annotations

import asyncio
import hashlib
import heapq
import json
import math
from collections.abc import AsyncGenerator, Callable, Mapping
from dataclasses import dataclass
from re import fullmatch
from typing import Any

from pydantic_ai import Agent, BinaryContent
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.usage import UsageLimits

from meristem.host_ports import (
    ApprovalWaiter,
    DecisionPort,
    DurableRunEventPublisher,
    PlanItem,
    ScaffoldPublisher,
    ToolCaller,
    ToolStepResult,
)

from .budget import SharedBudget
from .checkpoint import RunCheckpoint
from .live_events import (
    LineageBinding,
    lineage_event_fields,
    lineage_relationship,
    run_step,
)
from .state import (
    ApprovalRejected,
    ApprovalTimedOut,
    ApprovalWaiterUnavailable,
    BindingUnresolved,
    BranchVerdictInvalid,
    DecisionAbstained,
    DecisionPortUnavailable,
    DependencyPlan,
    GraphDeps,
    GraphState,
    RunEvent,
    RunPlan,
    RunStatus,
    RunTask,
    TaskEvent,
    TaskStatus,
    TaskTransitionRefused,
    ToolCallerUnavailable,
    ToolCallFailed,
    ToolCallRefused,
    ToolStep,
    transition,
)

TOOL_ARGUMENT_LIMIT = 64 * 1024
TOOL_RESULT_LIMIT = 256 * 1024


#: Legal task edges ``(from, event, to, guard facts)``; TE1 to TE7. The skip
#: edge admits any one of its two guard facts; the others admit exactly their
#: named fact.
_TASK_EDGES: tuple[
    tuple[TaskStatus, TaskEvent, TaskStatus, tuple[str, ...] | None], ...
] = (
    (TaskStatus.BLOCKED, TaskEvent.DEPS_MET, TaskStatus.READY, ("all_parents_done",)),
    (
        TaskStatus.BLOCKED,
        TaskEvent.SKIP,
        TaskStatus.SKIPPED,
        (
            "ancestor_failed_or_cancelled",
            "run_cancelling_or_failed",
            "arm_not_taken",
        ),
    ),
    (
        TaskStatus.READY,
        TaskEvent.SKIP,
        TaskStatus.SKIPPED,
        (
            "ancestor_failed_or_cancelled",
            "run_cancelling_or_failed",
            "arm_not_taken",
        ),
    ),
    (TaskStatus.READY, TaskEvent.DISPATCH, TaskStatus.ACTIVE, ("permit_held",)),
    (TaskStatus.ACTIVE, TaskEvent.COMPLETE, TaskStatus.DONE, ("run_running",)),
    (TaskStatus.ACTIVE, TaskEvent.FAIL, TaskStatus.FAILED, None),
    (TaskStatus.ACTIVE, TaskEvent.CANCEL, TaskStatus.CANCELLED, None),
)


def _task_event_by_value(value: Any) -> TaskEvent | None:
    if not isinstance(value, str):
        return None
    for event in TaskEvent:
        if event.value == value:
            return event
    return None


def transition_task(
    task_status: TaskStatus,
    event: TaskEvent | str,
    guard_facts: Mapping[str, bool] | None = None,
) -> TaskStatus:
    """The only writer of a task status: fire ``event`` on ``task_status``.

    Legal edges are TE1 to TE7; every other pair refuses with
    :class:`TaskTransitionRefused` (code ``TASK_TRANSITION_REFUSED``). The skip
    edge admits any one of its guard facts; the other guards admit exactly
    their named fact. A missing or false fact refuses with ``GuardFailed``.
    """
    facts = dict(guard_facts or {})
    if isinstance(event, str):
        fired = _task_event_by_value(event)
        if fired is None:
            raise TaskTransitionRefused(
                from_status=task_status, event=event, reason="UnknownEvent"
            )
    elif not isinstance(event, TaskEvent):
        raise TaskTransitionRefused(
            from_status=task_status, event=event, reason="UnknownEvent"
        )
    else:
        fired = event
    if task_status.is_terminal:
        raise TaskTransitionRefused(
            from_status=task_status, event=fired, reason="TerminalState"
        )
    for from_status, edge_event, target, guards in _TASK_EDGES:
        if from_status is task_status and edge_event is fired:
            break
    else:
        raise TaskTransitionRefused(
            from_status=task_status, event=fired, reason="IllegalTransition"
        )
    if guards is not None and not any(facts.get(name, False) for name in guards):
        raise TaskTransitionRefused(
            from_status=task_status, event=fired, reason="GuardFailed"
        )
    return target


@dataclass(frozen=True, slots=True)
class _ToolExecution:
    output: Any
    record: dict[str, Any]


#: Seconds an approval waits when the received step carries no timeout.
DEFAULT_APPROVAL_TIMEOUT_S = 86_400

_BRANCH_KINDS = ("exclusive", "inclusive", "parallel")
_CONTROL_KEYS = (
    ("branch", frozenset({"arms", "default_arm", "exhaustive", "kind"})),
    ("join", frozenset({"branch_task_id"})),
    ("decision", frozenset({"decision_iri", "inputs"})),
    ("approval", frozenset({"approver_role", "timeout_s"})),
)
_ARM_KEYS = frozenset({"condition", "target_task_id"})


def _control_kind(task: RunTask) -> str | None:
    """The one control kind a received task carries, or ``None``."""
    for kind, _ in _CONTROL_KEYS:
        if getattr(task, kind) is not None:
            return kind
    return None


@dataclass(frozen=True, slots=True)
class _ControlExecution:
    """A finished control step: its output and, for a branch, the taken arms."""

    output: Any
    kind: str
    taken: tuple[str, ...] | None = None


def arm_closure(
    root: str,
    nodes: Mapping[str, RunTask],
    children: Mapping[str, list[str]],
) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    """The tasks a not-taken arm skips and the joins that survive it.

    Starting at the arm target ``root``, every descendant is skipped except a
    join: a join survives a skipped parent and only counts that parent as met.
    Returns ``(skipped, edges)`` where each edge is ``(skipped_parent, join)``.
    An arm target that is itself a join skips nothing.
    """
    if nodes[root].join is not None:
        return (), ()
    skipped: list[str] = []
    edges: list[tuple[str, str]] = []
    seen = {root}
    stack = [root]
    while stack:
        key = stack.pop()
        skipped.append(key)
        for child in children[key]:
            if nodes[child].join is not None:
                edges.append((key, child))
            elif child not in seen:
                seen.add(child)
                stack.append(child)
    return tuple(skipped), tuple(edges)


def _decision_inputs(task: RunTask, outputs: Mapping[str, Any]) -> dict[str, Any]:
    """Bind each declared decision input from a direct prerequisite output."""
    assert task.decision is not None
    bound: dict[str, Any] = {}
    for reference in task.decision.get("inputs") or ():
        dependency, path = _reference_parts(reference, task.depends_on)
        bound[path[-1] if path else dependency] = _resolve_reference(
            reference,
            task_id=task.task_id,
            argument=reference,
            outputs=outputs,
            direct_prerequisites=task.depends_on,
        )
    return bound


async def _ask(
    decision_port: DecisionPort | None,
    question: Any,
    payload: Mapping[str, Any],
    task_id: str,
) -> Any:
    if decision_port is None:
        raise DecisionPortUnavailable("no host DecisionPort is bound")
    try:
        verdict = await decision_port(question, payload, task_id)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - host port owns failure details
        raise RuntimeError("decision port failed") from exc
    if verdict.abstention is not None:
        raise DecisionAbstained(verdict.abstention)
    return verdict


async def _apply_branch(
    task: RunTask,
    *,
    plan_digest: str,
    outputs: Mapping[str, Any],
    decision_port: DecisionPort | None,
    notify: Callable[..., Any],
) -> _ControlExecution:
    """Apply a branch step: ask for the verdict and name the taken arms.

    A parallel split takes every arm and never calls the port. Any other kind
    asks ``BranchSelect`` with the plan digest, the branch task id and the
    ordered outputs of the direct prerequisites. The harness validates the
    verdict against the declared arms and decides nothing.
    """
    assert task.branch is not None
    arms = tuple(arm["target_task_id"] for arm in task.branch["arms"])
    kind = task.branch["kind"]
    record_id = ""
    if kind == "parallel":
        taken = arms
    else:
        payload = {
            "plan_digest": plan_digest,
            "branch_task_id": task.task_id,
            "inputs": {dep: outputs[dep] for dep in task.depends_on},
        }
        verdict = await _ask(decision_port, "BranchSelect", payload, task.task_id)
        taken = tuple(verdict.taken_task_ids)
        record_id = verdict.record_id
        if (
            not taken
            or len(set(taken)) != len(taken)
            or any(target not in arms for target in taken)
            or (kind == "exclusive" and len(taken) != 1)
        ):
            raise BranchVerdictInvalid(
                f"verdict for branch {task.task_id!r} names an undeclared arm"
            )
    notify(
        "branch_verdict",
        task.task_id,
        taken_task_ids=list(taken),
        record_id=record_id,
        branch_kind=kind,
    )
    return _ControlExecution(
        {"taken_task_ids": list(taken), "record_id": record_id}, "branch", taken
    )


async def _apply_decision(
    task: RunTask,
    *,
    outputs: Mapping[str, Any],
    decision_port: DecisionPort | None,
) -> _ControlExecution:
    """Apply a decision step: the verdict is the step output. Never retried."""
    assert task.decision is not None
    payload = {
        "decision_iri": task.decision["decision_iri"],
        "inputs": _decision_inputs(task, outputs),
    }
    verdict = await _ask(decision_port, "DecisionTableEval", payload, task.task_id)
    return _ControlExecution(verdict.outputs, "decision")


async def _await_approval(
    task: RunTask,
    *,
    run_id: str,
    approval_waiter: ApprovalWaiter | None,
    notify: Callable[..., Any],
) -> _ControlExecution:
    """Wait for the host approval outcome; the host waiter owns the timeout."""
    assert task.approval is not None
    if approval_waiter is None:
        raise ApprovalWaiterUnavailable("no host ApprovalWaiter is bound")
    role = task.approval["approver_role"]
    timeout_s = task.approval.get("timeout_s")
    if timeout_s is None:
        timeout_s = DEFAULT_APPROVAL_TIMEOUT_S
    notify(
        "approval_wait_started",
        task.task_id,
        approver_role=role,
        timeout_s=timeout_s,
    )
    try:
        outcome = await approval_waiter(run_id, task.task_id, role, timeout_s)
    except asyncio.CancelledError:
        notify("approval_wait_ended", task.task_id, outcome="cancelled")
        raise
    except Exception as exc:  # noqa: BLE001 - host port owns failure details
        notify("approval_wait_ended", task.task_id, outcome="failed")
        raise RuntimeError("approval waiter failed") from exc
    notify("approval_wait_ended", task.task_id, outcome=outcome.verdict)
    if outcome.verdict == "reject":
        raise ApprovalRejected(f"approval {task.task_id!r} was rejected")
    if outcome.verdict == "timeout":
        raise ApprovalTimedOut(f"approval {task.task_id!r} timed out")
    return _ControlExecution(
        {
            "approver": outcome.approver,
            "verdict": outcome.verdict,
            "decided_at_ms": outcome.decided_at_ms,
        },
        "approval",
    )


def _task_label(task: RunTask) -> str:
    """One line naming a received task for the published plan."""
    if task.call is not None:
        detail = f"{task.call.server}.{task.call.tool}"
    else:
        lines = task.prompt.strip().splitlines()
        detail = lines[0][:160] if lines else ""
    return f"{task.task_id}: {detail}" if detail else task.task_id


def received_plan_items(graph: DependencyPlan) -> tuple[PlanItem, ...]:
    """The received dependency plan as the run's current plan, in declaration order."""
    return tuple(
        PlanItem(text=_task_label(task), status="pending") for task in graph.tasks
    )


def _json_bytes(value: Any) -> bytes:
    """Encode a structured value deterministically for size and digest checks."""
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ToolCallFailed("tool payload is not JSON-serializable") from exc


def _bounded(value: Any, limit: int) -> Any:
    """Keep a small value intact, or expose only its digest and byte count."""
    if value is None:
        return None
    encoded = _json_bytes(value)
    if len(encoded) <= limit:
        return value
    return {
        "digest": hashlib.sha256(encoded).hexdigest(),
        "bytes": len(encoded),
    }


def _path_tokens(path: str) -> tuple[str, ...]:
    """Parse the dotted JSON path form, accepting ``items[0]`` as a convenience."""
    if not path:
        return ()
    tokens: list[str] = []
    for component in path.split("."):
        if not component:
            raise ValueError("JSON path contains an empty component")
        remainder = component
        while "[" in remainder:
            prefix, suffix = remainder.split("[", 1)
            if prefix:
                tokens.append(prefix)
            if "]" not in suffix:
                raise ValueError("JSON path has an unterminated array index")
            index, remainder = suffix.split("]", 1)
            if not fullmatch(r"[0-9]+", index):
                raise ValueError("JSON path array indexes must be nonnegative integers")
            tokens.append(index)
        if remainder:
            tokens.append(remainder)
    return tuple(tokens)


def _reference_parts(
    reference: str, direct_prerequisites: tuple[str, ...]
) -> tuple[str, tuple[str, ...]]:
    """Split one ``steps.<id>.output[.<path>]`` reference."""
    if not isinstance(reference, str):
        raise ValueError("binding reference must be a string")
    for task_id in sorted(direct_prerequisites, key=len, reverse=True):
        prefix = f"steps.{task_id}.output"
        if reference == prefix:
            return task_id, ()
        if reference.startswith(f"{prefix}."):
            path = reference[len(prefix) + 1 :]
            if not path:
                raise ValueError("JSON path is empty")
            return task_id, _path_tokens(path)
    raise ValueError("binding must reference a direct prerequisite output")


def _validate_argument_value(
    value: Any,
    *,
    task_id: str,
    argument: str,
    direct_prerequisites: tuple[str, ...],
) -> None:
    if isinstance(value, Mapping):
        if "$ref" in value:
            if set(value) != {"$ref"}:
                raise ValueError("a binding object may contain only $ref")
            try:
                _reference_parts(value["$ref"], direct_prerequisites)
            except ValueError as exc:
                raise ValueError(
                    f"invalid binding at {argument or '/'} for task {task_id!r}: {exc}"
                ) from exc
            return
        if any(not isinstance(key, str) for key in value):
            raise ValueError("tool argument object keys must be strings")
        for key, child in value.items():
            _validate_argument_value(
                child,
                task_id=task_id,
                argument=f"{argument}/{key}",
                direct_prerequisites=direct_prerequisites,
            )
        return
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _validate_argument_value(
                child,
                task_id=task_id,
                argument=f"{argument}/{index}",
                direct_prerequisites=direct_prerequisites,
            )
        return
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("tool arguments must contain finite JSON numbers")
    if value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError("tool arguments must contain JSON-shaped literals or bindings")


def _lookup(value: Any, token: str) -> Any:
    if isinstance(value, Mapping):
        if token not in value:
            raise KeyError(token)
        return value[token]
    if isinstance(value, (list, tuple)):
        if not fullmatch(r"[0-9]+", token):
            raise TypeError(token)
        index = int(token)
        if index >= len(value):
            raise IndexError(index)
        return value[index]
    raise TypeError(token)


def _resolve_reference(
    reference: str,
    *,
    task_id: str,
    argument: str,
    outputs: Mapping[str, Any],
    direct_prerequisites: tuple[str, ...],
) -> Any:
    try:
        dependency, path = _reference_parts(reference, direct_prerequisites)
    except ValueError as exc:
        raise BindingUnresolved(task_id, argument, reference, str(exc)) from None
    try:
        value = outputs[dependency]
    except KeyError:
        raise BindingUnresolved(
            task_id, argument, reference, "prerequisite output is not completed"
        ) from None
    if path:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError, json.JSONDecodeError):
                raise BindingUnresolved(
                    task_id,
                    argument,
                    reference,
                    "prompt output is not JSON",
                ) from None
        for token in path:
            try:
                value = _lookup(value, token)
            except KeyError:
                raise BindingUnresolved(
                    task_id, argument, reference, f"missing path component {token!r}"
                ) from None
            except (IndexError, TypeError):
                raise BindingUnresolved(
                    task_id,
                    argument,
                    reference,
                    f"cannot traverse path component {token!r}",
                ) from None
    return value


def _resolve_value(
    value: Any,
    *,
    task_id: str,
    argument: str,
    outputs: Mapping[str, Any],
    direct_prerequisites: tuple[str, ...],
) -> Any:
    if isinstance(value, Mapping):
        if set(value) == {"$ref"}:
            return _resolve_reference(
                value["$ref"],
                task_id=task_id,
                argument=argument or "/",
                outputs=outputs,
                direct_prerequisites=direct_prerequisites,
            )
        if "$ref" in value:
            raise BindingUnresolved(
                task_id,
                argument or "/",
                str(value.get("$ref", "")),
                "a binding object may contain only $ref",
            )
        return {
            key: _resolve_value(
                child,
                task_id=task_id,
                argument=f"{argument}/{key}",
                outputs=outputs,
                direct_prerequisites=direct_prerequisites,
            )
            for key, child in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            _resolve_value(
                child,
                task_id=task_id,
                argument=f"{argument}/{index}",
                outputs=outputs,
                direct_prerequisites=direct_prerequisites,
            )
            for index, child in enumerate(value)
        ]
    return value


def resolve_tool_bindings(task: RunTask, outputs: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve a tool task's literals and completed-output references."""
    if task.call is None:
        raise ValueError("task has no tool call")
    resolved = _resolve_value(
        task.call.arguments,
        task_id=task.task_id,
        argument="",
        outputs=outputs,
        direct_prerequisites=task.depends_on,
    )
    if not isinstance(resolved, Mapping):
        raise BindingUnresolved(
            task.task_id,
            "/",
            "",
            "tool arguments must resolve to an object",
        )
    return dict(resolved)


def _record(
    task: RunTask,
    *,
    arguments: Any,
    outcome: str,
    result: Any = None,
    elapsed_seconds: float = 0.0,
    error: str | None = None,
    reference: str | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "task_id": task.task_id,
        "server": task.call.server if task.call is not None else "",
        "tool": task.call.tool if task.call is not None else "",
        "arguments": _bounded(arguments, TOOL_ARGUMENT_LIMIT),
        "outcome": outcome,
        "result": _bounded(result, TOOL_RESULT_LIMIT),
        "elapsed_seconds": max(0.0, float(elapsed_seconds)),
    }
    if error is not None:
        record["error"] = error
    if reference is not None:
        record["reference"] = reference
    return record


def _attach_record(error: BaseException, record: dict[str, Any]) -> BaseException:
    error.tool_step = record  # type: ignore[attr-defined]
    return error


def _tool_result(value: Any) -> ToolStepResult:
    return value if isinstance(value, ToolStepResult) else ToolStepResult(result=value)


def _output_text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, sort_keys=True)


def _validate_control_tasks(
    graph: DependencyPlan,
    nodes: Mapping[str, RunTask],
    children: Mapping[str, list[str]],
    *,
    decision_port: DecisionPort | None,
    approval_waiter: ApprovalWaiter | None,
) -> None:
    """Refuse malformed control steps, then missing host ports, before any task."""
    needs_decision = False
    needs_approval = False
    for task in graph.tasks:
        kinds = [kind for kind, _ in _CONTROL_KEYS if getattr(task, kind) is not None]
        if not kinds:
            continue
        kind = kinds[0]
        spec = getattr(task, kind)
        if len(kinds) > 1 or task.call is not None:
            raise ValueError("a control step carries one control kind and no call")
        if not isinstance(spec, Mapping) or not set(spec) <= dict(_CONTROL_KEYS)[kind]:
            raise ValueError(f"invalid {kind} step fields")
        if kind == "branch":
            arms = spec.get("arms")
            if (
                spec.get("kind") not in _BRANCH_KINDS
                or not isinstance(arms, (list, tuple))
                or not arms
                or len(arms) > 32
            ):
                raise ValueError("invalid branch step")
            targets: list[str] = []
            for arm in arms:
                if (
                    not isinstance(arm, Mapping)
                    or not set(arm) <= _ARM_KEYS
                    or not isinstance(arm.get("target_task_id"), str)
                    or arm["target_task_id"] not in children[task.task_id]
                ):
                    raise ValueError("branch arm must target a direct dependent")
                targets.append(arm["target_task_id"])
            default = spec.get("default_arm")
            if len(set(targets)) != len(targets) or (
                default is not None and default not in targets
            ):
                raise ValueError("branch arms must be distinct and hold the default")
            needs_decision = needs_decision or spec["kind"] != "parallel"
        elif kind == "join":
            source = nodes.get(spec.get("branch_task_id", ""))
            if source is None or source.branch is None:
                raise ValueError("join must name a branch step")
        elif kind == "decision":
            references = spec.get("inputs") or ()
            if (
                not isinstance(spec.get("decision_iri"), str)
                or not spec["decision_iri"]
            ):
                raise ValueError("decision step needs a decision IRI")
            names: list[str] = []
            for reference in references:
                dependency, path = _reference_parts(reference, task.depends_on)
                names.append(path[-1] if path else dependency)
            if len(set(names)) != len(names):
                raise ValueError("decision input names must be distinct")
            needs_decision = True
        else:
            timeout_s = spec.get("timeout_s")
            if (
                not isinstance(spec.get("approver_role"), str)
                or not spec["approver_role"]
            ):
                raise ValueError("approval step needs an approver role")
            if timeout_s is not None and (type(timeout_s) is not int or timeout_s < 0):
                raise ValueError("approval timeout must be a nonnegative integer")
            needs_approval = True
    if needs_decision and decision_port is None:
        raise DecisionPortUnavailable(
            "dependency plan contains a branch or decision step but no host "
            "DecisionPort is bound"
        )
    if needs_approval and approval_waiter is None:
        raise ApprovalWaiterUnavailable(
            "dependency plan contains an approval step but no host "
            "ApprovalWaiter is bound"
        )


def validate_dependency_plan(
    plan: RunPlan,
    *,
    tool_caller: ToolCaller | None = None,
    decision_port: DecisionPort | None = None,
    approval_waiter: ApprovalWaiter | None = None,
) -> None:
    """Refuse malformed or over-budget plans before constructing a model."""
    graph = plan.dependency_plan
    if graph is None:
        return
    if not isinstance(graph, DependencyPlan) or plan.steps:
        raise ValueError("dependency_plan must not be combined with serial steps")
    if (
        not isinstance(graph.tasks, tuple)
        or not isinstance(graph.output_task_id, str)
        or not isinstance(graph.plan_id, str)
    ):
        raise ValueError("dependency plan must have immutable tasks and string IDs")
    if type(graph.max_concurrency) is not int or graph.max_concurrency < 1:
        raise ValueError("max_concurrency must be a positive integer")
    if type(plan.stop.max_steps) is not int or plan.stop.max_steps < 1:
        raise ValueError("max_steps must be a positive integer")
    if not graph.tasks or len(graph.tasks) > plan.stop.max_steps:
        raise ValueError("dependency plan is empty or exceeds max_steps")
    if type(plan.harness.retries) is not int or plan.harness.retries < 0:
        raise ValueError("retries must be a nonnegative integer")
    if (
        type(plan.budget.max_concurrent_requests) is not int
        or plan.budget.max_concurrent_requests < 1
    ):
        raise ValueError("max_concurrent_requests must be a positive integer")
    for value in (
        plan.budget.request_limit,
        plan.budget.tool_calls_limit,
        plan.budget.total_tokens_limit,
    ):
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError("invalid dependency run budget")
    if (
        isinstance(plan.budget.timeout_seconds, bool)
        or not math.isfinite(plan.budget.timeout_seconds)
        or plan.budget.timeout_seconds < 0
    ):
        raise ValueError("invalid dependency run timeout")
    nodes: dict[str, RunTask] = {}
    tool_tasks: list[RunTask] = []
    for task in graph.tasks:
        if (
            not isinstance(task, RunTask)
            or not isinstance(task.task_id, str)
            or not task.task_id.strip()
            or task.task_id in nodes
            or not isinstance(task.prompt, str)
            or not isinstance(task.depends_on, tuple)
            or any(not isinstance(dep, str) for dep in task.depends_on)
            or len(set(task.depends_on)) != len(task.depends_on)
            or task.call is not None
            and not isinstance(task.call, ToolStep)
        ):
            raise ValueError("invalid or duplicate dependency task")
        if (
            task.call is None
            and not task.prompt.strip()
            and _control_kind(task) is None
        ):
            raise ValueError("prompt task must have a nonempty prompt")
        nodes[task.task_id] = task
        if task.call is not None:
            tool_tasks.append(task)
    if graph.output_task_id not in nodes:
        raise ValueError("output task is missing")
    indegree = {key: len(task.depends_on) for key, task in nodes.items()}
    children: dict[str, list[str]] = {key: [] for key in nodes}
    for task in graph.tasks:
        for dep in task.depends_on:
            if dep not in nodes:
                raise ValueError("dependency task is missing")
            children[dep].append(task.task_id)
    for task in tool_tasks:
        assert task.call is not None
        if (
            not isinstance(task.call.server, str)
            or not task.call.server.strip()
            or not isinstance(task.call.tool, str)
            or not task.call.tool.strip()
            or not isinstance(task.call.arguments, Mapping)
        ):
            raise ValueError("invalid tool step")
        _validate_argument_value(
            task.call.arguments,
            task_id=task.task_id,
            argument="",
            direct_prerequisites=task.depends_on,
        )
    ready = [key for key, count in indegree.items() if count == 0]
    visited = 0
    while ready:
        key = ready.pop()
        visited += 1
        for child in children[key]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    if visited != len(nodes):
        raise ValueError("dependency plan contains a cycle")
    if {key for key, values in children.items() if not values} != {
        graph.output_task_id
    }:
        raise ValueError("every task must contribute to the output task")
    _validate_control_tasks(
        graph,
        nodes,
        children,
        decision_port=decision_port,
        approval_waiter=approval_waiter,
    )
    if tool_tasks and tool_caller is None:
        raise ToolCallerUnavailable(
            "dependency plan contains tool steps but no host ToolCaller is bound"
        )


async def _execute_dependency_plan(
    agent: Agent[GraphDeps, Any],
    plan: RunPlan,
    state: GraphState,
    config: Mapping[str, Any],
    attachments: list[str | BinaryContent],
    budget: SharedBudget,
    emit: Callable[..., dict[str, Any]],
    tool_caller: ToolCaller | None = None,
    checkpoint: RunCheckpoint | None = None,
    event_publisher: DurableRunEventPublisher | None = None,
    child_records: Any = None,
    deadline_scope: asyncio.Timeout | None = None,
    scaffold_publisher: ScaffoldPublisher | None = None,
    decision_port: DecisionPort | None = None,
    approval_waiter: ApprovalWaiter | None = None,
) -> None:
    """Refill a free slot after any successful prerequisite, not a whole wave.

    Only active tasks have asyncio Tasks. Pending work is bounded plan data.
    Every exit cancels and awaits remaining children before returning control.
    """
    graph = plan.dependency_plan
    assert graph is not None
    nodes = {task.task_id: task for task in graph.tasks}
    order = {task.task_id: index for index, task in enumerate(graph.tasks)}
    indegree = {key: len(task.depends_on) for key, task in nodes.items()}
    children: dict[str, list[str]] = {key: [] for key in nodes}
    for node in graph.tasks:
        for dep in node.depends_on:
            children[dep].append(node.task_id)
    active: dict[str, asyncio.Task[Any]] = {}
    # One machine state per declared task; every change goes through
    # transition_task.  A restored checkpoint seeds the done tasks.
    task_state: dict[str, TaskStatus] = {key: TaskStatus.BLOCKED for key in nodes}
    if checkpoint is not None and checkpoint.restored:
        for key, stored in checkpoint.task_states.items():
            if key in nodes and stored is TaskStatus.DONE:
                task_state[key] = stored
    completed_tasks = {
        key for key, stored in task_state.items() if stored is TaskStatus.DONE
    }
    outputs: dict[str, Any] = (
        dict(checkpoint.dependency_outputs)
        if checkpoint is not None and checkpoint.restored
        else {}
    )
    for key in completed_tasks:
        for child in children[key]:
            indegree[child] -= 1
    ready: list[int] = []
    pending = set(nodes) - completed_tasks
    # Approval steps hold no concurrency slot while they wait on the host.
    waiting: set[str] = set()
    interrupted: BaseException | None = None
    drain_error: BaseException | None = None
    failures: list[BaseException] = []
    tool_order = [task.task_id for task in graph.tasks if task.call is not None]
    tool_terminal_buffer: dict[str, tuple[str, dict[str, Any]]] = {}
    # A tool effect may finish out of declaration order when the graph has
    # more than one active slot.  Keep its bounded result here until the
    # declaration terminal cursor reaches it.  This preserves parallel host
    # effects while making checkpoint ACKs, durable tool_result records, and
    # terminal run events one ordered publication sequence.
    tool_completion_buffer: dict[str, _ToolExecution] = {}
    tool_record_buffer: dict[str, tuple[RunTask, dict[str, Any], int]] = {}
    tool_terminal_cursor = 0
    tool_skipped: set[str] = set()
    step_by_task: dict[str, int] = {}
    checkpoint_acked: set[str] = set()
    publication_failed = False
    while (
        tool_terminal_cursor < len(tool_order)
        and tool_order[tool_terminal_cursor] in completed_tasks
    ):
        tool_terminal_cursor += 1

    async def progress(statuses: Mapping[str, str]) -> None:
        """Move the published plan items of tasks to their new status."""
        scaffold = state.scaffold
        if scaffold is None or not statuses:
            return
        _, items = scaffold.read_versioned()
        if not scaffold.received:
            return  # the model replaced the received plan; its edit stands
        updated = tuple(
            PlanItem(text=item.text, status=statuses.get(task.task_id, item.status))  # type: ignore[arg-type]
            for task, item in zip(graph.tasks, items, strict=True)
        )
        if updated == items:
            return
        revision, current = scaffold.replace_received(updated)
        if scaffold_publisher is not None:
            await scaffold_publisher.publish(revision, current)

    def sync_usage() -> None:
        state.usage.clear()
        state.add_usage(budget.usage)

    async def publish_durable(kind: str, content: Mapping[str, Any]) -> None:
        nonlocal publication_failed
        if event_publisher is None:
            return
        try:
            await event_publisher.publish(kind, content)
        except Exception as exc:  # noqa: BLE001 - sanitize host details
            publication_failed = True
            raise RuntimeError("durable run event publication failed") from exc

    async def publish_tool_record(
        task: RunTask, record: Mapping[str, Any], *, step: int
    ) -> None:
        """Publish the same bounded record that the terminal run event carries."""
        content: dict[str, Any] = {
            "step": step,
            "task_id": task.task_id,
            "tool_call_id": task.task_id,
            "name": record["tool"],
            "server": record["server"],
            "arguments": record["arguments"],
            "result": record["result"],
            "outcome": record["outcome"],
            "elapsed_seconds": record["elapsed_seconds"],
        }
        for key in ("error", "reference"):
            if key in record:
                content[key] = record[key]
        await publish_durable("tool_result", content)

    def event(kind: str, key: str, **fields: Any) -> dict[str, Any]:
        target = LineageBinding(
            run_id=state.run_id,
            node_kind="step",
            local_id=key,
            plan_id=graph.plan_id or None,
        )
        dependencies = tuple(
            lineage_relationship(
                "dependency",
                LineageBinding(
                    run_id=state.run_id,
                    node_kind="step",
                    local_id=dependency,
                    plan_id=graph.plan_id or None,
                ),
                target,
            )
            for dependency in nodes[key].depends_on
        )
        return emit(
            state,
            kind,
            task_id=key,
            parent_run_id=state.run_id,
            plan_id=graph.plan_id,
            depends_on=list(nodes[key].depends_on),
            **lineage_event_fields(target, dependencies),
            **fields,
        )

    def terminal_event(kind: str, key: str, **fields: Any) -> dict[str, Any] | None:
        """Emit prompt terminals immediately and tool records in declaration order."""
        if nodes[key].call is None:
            return event(kind, key, **fields)
        tool_terminal_buffer[key] = (kind, fields)
        return None

    def run_running() -> bool:
        """The guard fact of TE5: the run still runs and will complete normally."""
        return (
            state.status is RunStatus.RUNNING and not failures and interrupted is None
        )

    def skip_descendants(root: str) -> None:
        """A failed or cancelled task skips every descendant it leaves behind."""
        stack = list(children[root])
        while stack:
            key = stack.pop()
            if task_state[key].is_terminal or task_state[key] is TaskStatus.ACTIVE:
                continue
            task_state[key] = transition_task(
                task_state[key], TaskEvent.SKIP, {"ancestor_failed_or_cancelled": True}
            )
            stack.extend(children[key])

    def tool_event_fields(record: dict[str, Any] | None) -> dict[str, Any]:
        """Expose the bounded record both nested and as stable event fields."""
        if record is None:
            return {}
        fields = {
            "tool_step": record,
            "server": record["server"],
            "tool": record["tool"],
            "arguments": record["arguments"],
            "outcome": record["outcome"],
            "result": record["result"],
            "elapsed_seconds": record["elapsed_seconds"],
        }
        for key in ("error", "reference"):
            if key in record:
                fields[key] = record[key]
        return fields

    async def flush_tool_terminals(
        *, publish_records: bool = True, emit_completed: bool = True
    ) -> tuple[str, ...]:
        """Publish buffered tool terminals in declaration order.

        Effects run concurrently, but a completed result is only exposed once
        every earlier declared tool has reached its own terminal publication.
        The checkpoint ACK intentionally precedes the durable result and the
        public terminal event for each step.
        """
        nonlocal tool_terminal_cursor
        released: list[str] = []
        while tool_terminal_cursor < len(tool_order):
            key = tool_order[tool_terminal_cursor]
            if key in tool_skipped:
                # A not-taken arm's tool never runs; the cursor passes it.
                tool_terminal_cursor += 1
                continue
            result = tool_completion_buffer.get(key)
            buffered = tool_terminal_buffer.get(key)
            record_info = tool_record_buffer.get(key)
            if result is None and buffered is None:
                break

            if result is not None:
                tool_completion_buffer.pop(key, None)
                record = result.record
                if not publish_records and not emit_completed:
                    tool_terminal_buffer.pop(key, None)
                    tool_record_buffer.pop(key, None)
                    tool_terminal_cursor += 1
                    continue
                if publish_records:
                    try:
                        if checkpoint is not None and key not in checkpoint_acked:
                            sync_usage()
                            checkpoint.record_completed(key, result.output)
                            await checkpoint.save("completed")
                            checkpoint_acked.add(key)
                        output_text = _output_text(result.output)
                        if key == graph.output_task_id:
                            state.outputs = [output_text]
                        await publish_tool_record(
                            nodes[key], record, step=step_by_task[key]
                        )
                        if key == graph.output_task_id:
                            if any(
                                marker in output_text
                                for marker in plan.stop.stop_markers
                            ):
                                if state.status is RunStatus.RUNNING:
                                    state.status = transition(
                                        state.status, RunEvent.STOP_REQUESTED
                                    )
                            elif state.status is RunStatus.RUNNING:
                                state.status = transition(
                                    state.status,
                                    RunEvent.FINISH,
                                    {"output_task_complete": True},
                                )
                            if checkpoint is not None and state.status.is_terminal:
                                # The terminal snapshot records the run state
                                # exactly; it is never inferred from Running.
                                await checkpoint.save("completed")
                    except BaseException:
                        # Do not expose a terminal event after a durable
                        # publication failure; the failed record remains
                        # uncertain and must be retried from its checkpoint.
                        tool_terminal_buffer.pop(key, None)
                        raise
                if buffered is None:
                    buffered = (
                        "task_completed",
                        {
                            "status": "completed",
                            "output": record["result"],
                            "tool_step": record,
                            "server": record["server"],
                            "tool": record["tool"],
                            "arguments": record["arguments"],
                            "outcome": record["outcome"],
                            "result": record["result"],
                            "elapsed_seconds": record["elapsed_seconds"],
                        },
                    )
                tool_terminal_buffer.pop(key, None)
                if publish_records:
                    released.append(key)
            else:
                assert buffered is not None
                tool_terminal_buffer.pop(key, None)
                tool_record_buffer.pop(key, None)
                if not emit_completed:
                    tool_terminal_cursor += 1
                    continue
                if record_info is not None:
                    if publish_records:
                        record_task, record, record_step = record_info
                        await publish_tool_record(record_task, record, step=record_step)

            buffered_kind, buffered_fields = buffered
            event(buffered_kind, key, **buffered_fields)
            if buffered_kind == "task_completed":
                # A successful tool completed without a durable publisher has
                # already recorded its checkpoint/result above.  Releasing
                # its dependants still belongs to this ordered flush, while
                # failed and cancelled terminals must never release them.
                released.append(key)
            tool_terminal_cursor += 1
        return tuple(released)

    async def execute(task: RunTask) -> Any:
        # A join reads only the arms it waited for; a skipped parent has no output.
        dependency_outputs = {
            dep: outputs[dep] for dep in task.depends_on if dep in outputs
        }
        task_step = step_by_task[task.task_id]
        if task.branch is not None:
            return await _apply_branch(
                task,
                plan_digest=graph.plan_id,
                outputs=outputs,
                decision_port=decision_port,
                notify=event,
            )
        if task.decision is not None:
            return await _apply_decision(
                task, outputs=outputs, decision_port=decision_port
            )
        if task.approval is not None:
            return await _await_approval(
                task,
                run_id=state.run_id,
                approval_waiter=approval_waiter,
                notify=event,
            )
        if task.join is not None and task.call is None and not task.prompt.strip():
            return _ControlExecution(dependency_outputs, "join")
        if task.call is not None:
            assert tool_caller is not None
            started = asyncio.get_running_loop().time()
            try:
                arguments = resolve_tool_bindings(task, outputs)
            except BindingUnresolved as exc:
                record = _record(
                    task,
                    arguments=None,
                    outcome="failed",
                    elapsed_seconds=asyncio.get_running_loop().time() - started,
                    error=str(exc),
                    reference=exc.reference,
                )
                tool_record_buffer[task.task_id] = (task, record, task_step)
                raise _attach_record(exc, record) from None
            try:
                subset = (plan.harness.action_space or {}).get("tool_subset")
                if subset is not None and task.call.tool not in subset:
                    raise ToolCallRefused("TOOL_NOT_ALLOWED")
                budget.reserve_tool_call()
                await publish_durable(
                    "tool_call",
                    {
                        "step": task_step,
                        "task_id": task.task_id,
                        "tool_call_id": task.task_id,
                        "name": task.call.tool,
                        "server": task.call.server,
                        "arguments": _bounded(arguments, TOOL_ARGUMENT_LIMIT),
                        "args_valid": True,
                    },
                )
                raw_result = await tool_caller(
                    task.call.server,
                    task.call.tool,
                    arguments,
                    task.task_id,
                )
                result = _tool_result(raw_result)
                if result.outcome == "refused":
                    raise ToolCallRefused(result.error or "tool call refused")
                if result.outcome == "failed":
                    raise ToolCallFailed(result.error or "tool call failed")
            except asyncio.CancelledError as exc:
                record = _record(
                    task,
                    arguments=locals().get("arguments"),
                    outcome="failed",
                    elapsed_seconds=asyncio.get_running_loop().time() - started,
                    error="tool call cancelled",
                )
                raise _attach_record(exc, record) from None
            except UsageLimitExceeded as exc:
                record = _record(
                    task,
                    arguments=locals().get("arguments"),
                    outcome="refused",
                    elapsed_seconds=asyncio.get_running_loop().time() - started,
                    error=str(exc),
                )
                tool_record_buffer[task.task_id] = (task, record, task_step)
                raise _attach_record(exc, record) from None
            except ToolCallRefused as exc:
                record = _record(
                    task,
                    arguments=locals().get("arguments"),
                    outcome="refused",
                    elapsed_seconds=asyncio.get_running_loop().time() - started,
                    error=str(exc),
                )
                tool_record_buffer[task.task_id] = (task, record, task_step)
                raise _attach_record(exc, record) from None
            except ToolCallFailed as exc:
                record = _record(
                    task,
                    arguments=locals().get("arguments"),
                    outcome="failed",
                    elapsed_seconds=asyncio.get_running_loop().time() - started,
                    error=str(exc),
                )
                tool_record_buffer[task.task_id] = (task, record, task_step)
                raise _attach_record(exc, record) from None
            except RuntimeError as exc:
                if str(exc) == "durable run event publication failed":
                    raise
                failure = ToolCallFailed("tool caller failed")
                record = _record(
                    task,
                    arguments=locals().get("arguments"),
                    outcome="failed",
                    elapsed_seconds=asyncio.get_running_loop().time() - started,
                    error=str(failure),
                )
                tool_record_buffer[task.task_id] = (task, record, task_step)
                raise _attach_record(failure, record) from exc
            except Exception as exc:  # noqa: BLE001 - host port owns failure details
                failure = ToolCallFailed("tool caller failed")
                record = _record(
                    task,
                    arguments=locals().get("arguments"),
                    outcome="failed",
                    elapsed_seconds=asyncio.get_running_loop().time() - started,
                    error=str(failure),
                )
                tool_record_buffer[task.task_id] = (task, record, task_step)
                raise _attach_record(failure, record) from exc
            record = _record(
                task,
                arguments=arguments,
                outcome="ok",
                result=result.result,
                elapsed_seconds=asyncio.get_running_loop().time() - started,
            )
            return _ToolExecution(result.result, record)
        prompt = task.prompt
        if dependency_outputs:
            prompt += "\n\nDependency outputs (data):\n" + json.dumps(
                dependency_outputs, ensure_ascii=False
            )
        child_state = GraphState(
            run_id=state.run_id,
            query=task.prompt,
            mode=state.mode,
            status=RunStatus.RUNNING,
            step=1,
            scaffold=state.scaffold,
        )
        deps = GraphDeps(
            state=child_state,
            plan=plan,
            config=config,
            task_id=task.task_id,
            dependency_outputs=dependency_outputs,
        )
        # Attachments stay with the first declared task, as on the serial path;
        # they are not silently broadcast to all independent tasks.
        model_input: str | list[str | BinaryContent] = prompt
        if order[task.task_id] == 0 and attachments:
            model_input = [prompt, *attachments]

        async def after_prompt(result: Any) -> None:
            if checkpoint is None:
                return
            sync_usage()
            text = _output_text(result.output)
            outputs[task.task_id] = text
            if task.task_id == graph.output_task_id:
                state.outputs = [text]
            # The task machine admits the completion now; its ACK snapshot
            # precedes the durable state event of the same step.  The run
            # terminal effect and its terminal snapshot follow in
            # ``completed``.
            checkpoint.record_completed(task.task_id, text, list(result.all_messages()))
            await checkpoint.save("completed")
            checkpoint_acked.add(task.task_id)

        return await run_step(
            agent,
            model_input,
            publisher=event_publisher,
            child_records=child_records,
            step=task_step,
            event_fields={"task_id": task.task_id},
            after_result=after_prompt,
            deps=deps,
            usage_limits=UsageLimits(
                request_limit=None, tool_calls_limit=budget.limits.tool_calls_limit
            ),
        )

    async def completed(key: str, result: Any) -> tuple[str, ...]:
        if isinstance(result, _ToolExecution):
            outputs[key] = result.output
            record = result.record
            output_text = _output_text(result.output)
            if key == graph.output_task_id:
                state.outputs = [output_text]
            if event_publisher is not None:
                # The durable publisher path records and stops or finishes
                # the run in declaration order, inside the ordered flush.
                tool_completion_buffer[key] = result
                terminal_event(
                    "task_completed",
                    key,
                    status="completed",
                    output=record["result"],
                    tool_step=record,
                    server=record["server"],
                    tool=record["tool"],
                    arguments=record["arguments"],
                    outcome=record["outcome"],
                    result=record["result"],
                    elapsed_seconds=record["elapsed_seconds"],
                )
                return await flush_tool_terminals()
            if checkpoint is not None and key not in checkpoint_acked:
                sync_usage()
                checkpoint.record_completed(key, result.output)
                await checkpoint.save("completed")
                checkpoint_acked.add(key)
            await publish_tool_record(nodes[key], record, step=step_by_task[key])
            if key == graph.output_task_id:
                if any(marker in output_text for marker in plan.stop.stop_markers):
                    if state.status is RunStatus.RUNNING:
                        state.status = transition(state.status, RunEvent.STOP_REQUESTED)
                elif state.status is RunStatus.RUNNING:
                    state.status = transition(
                        state.status,
                        RunEvent.FINISH,
                        {"output_task_complete": True},
                    )
                if checkpoint is not None and state.status.is_terminal:
                    # The terminal snapshot records the run state exactly;
                    # it is never inferred from Running.
                    await checkpoint.save("completed")
            terminal_event(
                "task_completed",
                key,
                status="completed",
                output=record["result"],
                tool_step=record,
                server=record["server"],
                tool=record["tool"],
                arguments=record["arguments"],
                outcome=record["outcome"],
                result=record["result"],
                elapsed_seconds=record["elapsed_seconds"],
            )
            return await flush_tool_terminals()
        output = result.output
        text = output if isinstance(output, str) else json.dumps(output, sort_keys=True)
        control = isinstance(result, _ControlExecution)
        history = None if control else list(result.all_messages())
        outputs[key] = text
        if key == graph.output_task_id:
            state.outputs = [text]
        if checkpoint is not None and key not in checkpoint_acked:
            sync_usage()
            checkpoint.record_completed(key, text, history)
            checkpoint_acked.add(key)
            await checkpoint.save("completed")
        if key == graph.output_task_id:
            if any(marker in text for marker in plan.stop.stop_markers):
                if state.status is RunStatus.RUNNING:
                    state.status = transition(state.status, RunEvent.STOP_REQUESTED)
            elif state.status is RunStatus.RUNNING:
                state.status = transition(
                    state.status, RunEvent.FINISH, {"output_task_complete": True}
                )
            if checkpoint is not None and state.status.is_terminal:
                # The terminal snapshot records the run state exactly; it is
                # never inferred from Running.
                await checkpoint.save("completed")
        event("task_completed", key, status="completed", output=text)
        if isinstance(result, _ControlExecution) and result.taken is not None:
            if nodes[key].branch["kind"] != "parallel":  # type: ignore[index]
                apply_taken(key, result.taken)
                # A tool that finished behind a now-skipped arm's tool in
                # declaration order is released only after the skip.
                return (key, *await flush_tool_terminals())
        return (key,)

    def make_ready(key: str) -> None:
        task_state[key] = transition_task(
            task_state[key], TaskEvent.DEPS_MET, {"all_parents_done": True}
        )
        heapq.heappush(ready, order[key])

    def skip_one(key: str, arm: str, branch: str) -> None:
        task_state[key] = transition_task(
            task_state[key], TaskEvent.SKIP, {"arm_not_taken": True}
        )
        pending.discard(key)
        tool_skipped.add(key)
        event(
            "task_skipped",
            key,
            status="skipped",
            not_taken_arm=arm,
            branch_task_id=branch,
        )

    def settle_join(join: str, arm: str, branch: str) -> None:
        """A parent of ``join`` was skipped: count it as met."""
        if task_state[join].is_terminal:
            return
        indegree[join] -= 1
        if indegree[join] > 0:
            return
        if all(task_state[dep] is TaskStatus.SKIPPED for dep in nodes[join].depends_on):
            # Every arm into this join was not taken, so it is not taken.
            skip_one(join, arm, branch)
            for child in children[join]:
                if nodes[child].join is not None:
                    settle_join(child, arm, branch)
                else:
                    skip_arm(child, branch, arm)
        else:
            make_ready(join)

    def skip_arm(root: str, branch: str, arm: str | None = None) -> None:
        """Skip everything only a not-taken arm reaches; joins survive it."""
        skipped, edges = arm_closure(root, nodes, children)
        fresh = [key for key in skipped if not task_state[key].is_terminal]
        for key in fresh:
            skip_one(key, arm or root, branch)
        for parent, join in edges:
            if parent in fresh:
                settle_join(join, arm or root, branch)

    def apply_taken(branch: str, taken: tuple[str, ...]) -> None:
        """Skip every declared arm the verdict did not take."""
        spec = nodes[branch].branch
        assert spec is not None
        for arm in spec["arms"]:
            target = arm["target_task_id"]
            if target not in taken:
                skip_arm(target, branch)

    # A restored branch re-applies its recorded verdict before anything starts.
    for key in sorted(completed_tasks, key=order.__getitem__):
        spec = nodes[key].branch
        if spec is not None and spec["kind"] != "parallel":
            apply_taken(key, tuple(json.loads(outputs[key])["taken_task_ids"]))
    for key in [
        key
        for key, count in indegree.items()
        if count == 0 and task_state[key] is TaskStatus.BLOCKED
    ]:
        make_ready(key)

    try:
        await progress({key: "done" for key in completed_tasks})
        while ready or active:
            while ready and len(active) - len(waiting) < graph.max_concurrency:
                node = graph.tasks[heapq.heappop(ready)]
                pending.remove(node.task_id)
                state.step += 1
                step_by_task[node.task_id] = state.step
                if checkpoint is not None:
                    sync_usage()
                    await checkpoint.save("step_inflight", task_id=node.task_id)
                # A free slot is the model-call permit of TE4.
                task_state[node.task_id] = transition_task(
                    task_state[node.task_id],
                    TaskEvent.DISPATCH,
                    {"permit_held": True},
                )
                if node.approval is not None:
                    waiting.add(node.task_id)
                active[node.task_id] = asyncio.create_task(
                    execute(node), name=f"run:{state.run_id}:task:{node.task_id}"
                )
                event("task_started", node.task_id, status="running")
                await progress({node.task_id: "in_progress"})
            done, _ = await asyncio.wait(
                active.values(), return_when=asyncio.FIRST_COMPLETED
            )
            for key in sorted(
                (key for key, task in active.items() if task in done),
                key=order.__getitem__,
            ):
                task = active.pop(key)
                waiting.discard(key)
                try:
                    result = task.result()
                except asyncio.CancelledError as exc:
                    failures.append(exc)
                    task_state[key] = transition_task(task_state[key], TaskEvent.CANCEL)
                    skip_descendants(key)
                    record = getattr(exc, "tool_step", None)
                    terminal_event(
                        "task_cancelled",
                        key,
                        status="cancelled",
                        **tool_event_fields(record),
                    )
                except Exception as exc:  # noqa: BLE001 - parent owns failure policy
                    failures.append(exc)
                    task_state[key] = transition_task(task_state[key], TaskEvent.FAIL)
                    skip_descendants(key)
                    if str(exc) == "durable run event publication failed":
                        publication_failed = True
                    record = getattr(exc, "tool_step", None)
                    terminal_event(
                        "task_failed",
                        key,
                        status="failed",
                        error_type=type(exc).__name__,
                        **tool_event_fields(record),
                    )
                else:
                    try:
                        task_state[key] = transition_task(
                            task_state[key],
                            TaskEvent.COMPLETE,
                            {"run_running": run_running()},
                        )
                    except TaskTransitionRefused:
                        # A sibling that finishes after a failure in this
                        # round has its complete refused by the guard; its
                        # output is discarded with the failed run.
                        continue
                    released = await completed(key, result)
                    await progress({key: "done" for key in released})
                    for released_key in released:
                        if state.status is RunStatus.RUNNING and any(
                            m in _output_text(outputs[released_key])
                            for m in plan.stop.stop_markers
                        ):
                            state.status = transition(
                                state.status, RunEvent.STOP_REQUESTED
                            )
                        for child in children[released_key]:
                            if task_state[child].is_terminal:
                                continue  # a not-taken arm target
                            indegree[child] -= 1
                            if indegree[child] == 0:
                                # Every parent of a released child is done
                                # or a skipped arm the join does not wait for.
                                make_ready(child)
            if failures:
                if len(failures) > 1:
                    failures[0].all_failures = tuple(failures)  # type: ignore[attr-defined]
                raise failures[0]
            if state.status.stop_reason is not None:
                break
        if graph.output_task_id in outputs and not state.outputs:
            state.outputs.append(_output_text(outputs[graph.output_task_id]))
    except BaseException as exc:
        interrupted = exc
    finally:
        # No terminal parent event or released host scope can precede cleanup.
        # A fired deadline is a budget stop (RE6); a host cancellation moves
        # the run to Cancelling (RE8). The drain below then cancels every
        # Active task and skips every Blocked or Ready one.
        if state.status is RunStatus.RUNNING and isinstance(
            interrupted, asyncio.CancelledError
        ):
            if deadline_scope is not None and deadline_scope.expired():
                state.status = transition(state.status, RunEvent.STOP_BUDGET)
            else:
                state.status = transition(state.status, RunEvent.CANCEL)
        for task in active.values():
            if not task.done():
                task.cancel()
        if active:
            drain = asyncio.gather(*active.values(), return_exceptions=True)
            while not drain.done():
                try:
                    await asyncio.shield(drain)
                except asyncio.CancelledError as exc:
                    # Repeated parent cancellation must not detach children
                    # while their own finally blocks release host resources.
                    interrupted = interrupted or exc
        for key, task in active.items():
            if task.cancelled():
                task_state[key] = transition_task(task_state[key], TaskEvent.CANCEL)
                skip_descendants(key)
                terminal_event("task_cancelled", key, status="cancelled")
            elif task.exception() is not None:
                task_exc = task.exception()
                if not isinstance(task_exc, (asyncio.CancelledError, GeneratorExit)):
                    # A drain error is a run-level failure, not only a task
                    # failure; it is raised after the drain if the run is
                    # still Cancelling (RE10).
                    drain_error = drain_error or task_exc
                task_state[key] = transition_task(task_state[key], TaskEvent.FAIL)
                skip_descendants(key)
                record = getattr(task_exc, "tool_step", None)
                terminal_event(
                    "task_failed",
                    key,
                    status="failed",
                    error_type=type(task.exception()).__name__,
                    **tool_event_fields(record),
                )
            elif not publication_failed:
                # A sibling that finishes after the run failed or cancelled
                # has its complete refused by the guard; its output is
                # discarded.
                try:
                    task_state[key] = transition_task(
                        task_state[key],
                        TaskEvent.COMPLETE,
                        {"run_running": run_running()},
                    )
                except TaskTransitionRefused:
                    continue
                await completed(key, task.result())
        await flush_tool_terminals(
            publish_records=not publication_failed
            and not isinstance(interrupted, asyncio.CancelledError),
            emit_completed=not publication_failed,
        )
        if not publication_failed:
            for key in sorted(pending, key=order.__getitem__):
                if (
                    state.status is RunStatus.CANCELLING
                    and not task_state[key].is_terminal
                ):
                    task_state[key] = transition_task(
                        task_state[key],
                        TaskEvent.SKIP,
                        {"run_cancelling_or_failed": True},
                    )
                event("task_blocked", key, status="blocked")
        # This seeded ledger is the whole run, not a delta to add again.
        state.usage.clear()
        state.add_usage(budget.usage)
        # A drain error is a run-level failure (RE10): a cancellation that
        # cannot drain is not a clean stop, so the plain exception escapes
        # the finally ahead of the in-flight cancellation.
        if drain_error is not None and state.status is RunStatus.CANCELLING:
            raise drain_error
        # Every task is no longer Active, so the cancellation has fully
        # drained (RE9).
        if state.status is RunStatus.CANCELLING:
            state.status = transition(
                state.status, RunEvent.DRAIN_COMPLETE, {"no_active_task": True}
            )
    if interrupted is not None:
        raise interrupted


async def iterate_dependency_plan(
    agent: Agent[GraphDeps, Any],
    plan: RunPlan,
    state: GraphState,
    config: Mapping[str, Any],
    attachments: list[str | BinaryContent],
    budget: SharedBudget,
    emit: Callable[..., dict[str, Any]],
    *,
    deadline: float | None,
    tool_caller: ToolCaller | None = None,
    checkpoint: RunCheckpoint | None = None,
    event_publisher: DurableRunEventPublisher | None = None,
    child_records: Any = None,
    scaffold_publisher: ScaffoldPublisher | None = None,
    decision_port: DecisionPort | None = None,
    approval_waiter: ApprovalWaiter | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Stream public events without making child dispatch wait for a viewer.

    Each declared task emits at most four events (start, a verdict or the two
    approval wait events, and its terminal; or only blocked or skipped), so a
    finite queue sized to that invariant cannot block execution. There is one
    driver task, never a task per pending node or per event.
    """
    graph = plan.dependency_plan
    assert graph is not None
    events: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(
        maxsize=4 * len(graph.tasks) + 1
    )

    def enqueue(*args: Any, **kwargs: Any) -> dict[str, Any]:
        value = emit(*args, **kwargs)
        events.put_nowait(value)
        return value

    async def drive() -> None:
        try:
            # Own the deadline where work runs, not in the external reader's
            # task: a reader may pause indefinitely between iterator reads.
            scope = asyncio.timeout_at(deadline)
            async with scope:
                await _execute_dependency_plan(
                    agent,
                    plan,
                    state,
                    config,
                    attachments,
                    budget,
                    enqueue,
                    tool_caller,
                    checkpoint,
                    event_publisher,
                    child_records,
                    deadline_scope=scope,
                    scaffold_publisher=scaffold_publisher,
                    decision_port=decision_port,
                    approval_waiter=approval_waiter,
                )
        finally:
            events.put_nowait(None)

    driver = asyncio.create_task(drive(), name=f"run:{state.run_id}:dependency-driver")
    interrupted: BaseException | None = None
    try:
        while (event := await events.get()) is not None:
            yield event
        await driver
    except BaseException as exc:
        interrupted = exc
    finally:
        if not driver.done():
            driver.cancel()
        drain = asyncio.gather(driver, return_exceptions=True)
        while not drain.done():
            try:
                await asyncio.shield(drain)
            except asyncio.CancelledError as exc:
                interrupted = interrupted or exc
        # A plain exception that escaped the driver's drain is a run failure
        # (RE10); it is not masked by the host cancellation that started
        # the drain.
        if isinstance(interrupted, (asyncio.CancelledError, GeneratorExit)):
            outcome = drain.result()[0]
            if isinstance(outcome, BaseException) and not isinstance(
                outcome, (asyncio.CancelledError, GeneratorExit)
            ):
                interrupted = outcome
    if interrupted is not None:
        raise interrupted
