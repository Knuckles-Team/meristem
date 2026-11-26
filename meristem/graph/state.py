"""Typed state of one agent run: the request, the plan it receives, the result.

The harness never chooses any field of a :class:`RunPlan`. The host (arbora,
answering from medulla) supplies the plan through
:attr:`meristem.host_ports.HostPorts.decide`. Without a
host, :func:`default_run_plan` builds the documented fallback plan from the
request and the graph configuration only.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, Literal

from pydantic_ai import BinaryContent
from pydantic_ai.usage import RunUsage

from meristem.host_ports import (
    ClientCapabilityTransport,
    HostPorts,
    PlanState,
)

from .metrics import RunMetrics

if TYPE_CHECKING:
    from meristem.harness.client_capabilities import ClientToolLimits
    from meristem.harness.run_lifecycle import RunLifecycle

#: The closed refusal reasons of both in-run machines, matching the medulla
#: ``TransitionRefused`` record (MEDULLA-ORCH-VOCAB-R811.4).
RefusalReason = Literal[
    "IllegalTransition", "GuardFailed", "TerminalState", "UnknownEvent"
]

RUN_TRANSITION_REFUSED: Final = "RUN_TRANSITION_REFUSED"
TASK_TRANSITION_REFUSED: Final = "TASK_TRANSITION_REFUSED"


class RunStatus(Enum):
    """The closed states of one run. Terminals derive, they are never stored."""

    PENDING = "pending"
    RUNNING = "running"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED_REQUESTED = "stopped_requested"
    STOPPED_BUDGET = "stopped_budget"
    STOPPED_CANCELLED = "stopped_cancelled"

    @property
    def stop_reason(self) -> str | None:
        """The typed reason of a stopped run; ``None`` for every other state."""
        reasons = {
            RunStatus.STOPPED_REQUESTED: "requested",
            RunStatus.STOPPED_BUDGET: "budget",
            RunStatus.STOPPED_CANCELLED: "cancelled",
        }
        return reasons.get(self)

    @property
    def is_terminal(self) -> bool:
        return self in (
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.STOPPED_REQUESTED,
            RunStatus.STOPPED_BUDGET,
            RunStatus.STOPPED_CANCELLED,
        )


class RunEvent(Enum):
    """The closed events of the run machine; the value is the wire form."""

    START = "start"
    FINISH = "finish"
    STOP_REQUESTED = "stop_requested"
    STOP_BUDGET = "stop_budget"
    CANCEL = "cancel"
    DRAIN_COMPLETE = "drain_complete"
    FAIL = "fail"


class TaskStatus(Enum):
    """The closed states of one fan-out task."""

    BLOCKED = "blocked"
    READY = "ready"
    ACTIVE = "active"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"

    @property
    def is_terminal(self) -> bool:
        return self in (
            TaskStatus.DONE,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.SKIPPED,
        )


class TaskEvent(Enum):
    """The closed events of the task machine; the value is the wire form."""

    DEPS_MET = "deps_met"
    DISPATCH = "dispatch"
    COMPLETE = "complete"
    FAIL = "fail"
    CANCEL = "cancel"
    SKIP = "skip"


class RunTransitionRefused(RuntimeError):
    """A run status event the run machine does not admit."""

    machine = "run"
    code: Literal["RUN_TRANSITION_REFUSED"] = RUN_TRANSITION_REFUSED

    def __init__(
        self,
        *,
        from_status: RunStatus,
        event: RunEvent | str,
        reason: RefusalReason,
    ) -> None:
        self.from_status = from_status
        self.event = event
        self.reason = reason
        super().__init__(
            f"run transition refused: {from_status.value} + {event} ({reason})"
        )


class TaskTransitionRefused(RuntimeError):
    """A task status event the task machine does not admit."""

    machine = "task"
    code: Literal["TASK_TRANSITION_REFUSED"] = TASK_TRANSITION_REFUSED

    def __init__(
        self,
        *,
        from_status: TaskStatus,
        event: TaskEvent | str,
        reason: RefusalReason,
    ) -> None:
        self.from_status = from_status
        self.event = event
        self.reason = reason
        super().__init__(
            f"task transition refused: {from_status.value} + {event} ({reason})"
        )


#: Legal run edges ``(from, event, to, guard fact)``; RE1 to RE10.
_RUN_EDGES: tuple[tuple[RunStatus, RunEvent, RunStatus, str | None], ...] = (
    (RunStatus.PENDING, RunEvent.START, RunStatus.RUNNING, "plan_received"),
    (RunStatus.PENDING, RunEvent.FAIL, RunStatus.FAILED, None),
    (RunStatus.PENDING, RunEvent.CANCEL, RunStatus.STOPPED_CANCELLED, None),
    (RunStatus.RUNNING, RunEvent.FINISH, RunStatus.COMPLETED, "output_task_complete"),
    (RunStatus.RUNNING, RunEvent.STOP_REQUESTED, RunStatus.STOPPED_REQUESTED, None),
    (RunStatus.RUNNING, RunEvent.STOP_BUDGET, RunStatus.STOPPED_BUDGET, None),
    (RunStatus.RUNNING, RunEvent.FAIL, RunStatus.FAILED, None),
    (RunStatus.RUNNING, RunEvent.CANCEL, RunStatus.CANCELLING, None),
    (
        RunStatus.CANCELLING,
        RunEvent.DRAIN_COMPLETE,
        RunStatus.STOPPED_CANCELLED,
        "no_active_task",
    ),
    (RunStatus.CANCELLING, RunEvent.FAIL, RunStatus.FAILED, None),
)

#: The one table mapping terminal run states onto the medulla work-item
#: vocabulary (MEDULLA-ORCH-VOCAB-R812.1). Stopped(requested) is a distinct
#: terminal that keeps its reason; it maps to Cancelled, never to Succeeded.
RUN_TERMINAL_VOCAB: Mapping[RunStatus, str] = MappingProxyType(
    {
        RunStatus.COMPLETED: "Succeeded",
        RunStatus.FAILED: "Failed",
        RunStatus.STOPPED_CANCELLED: "Cancelled",
        RunStatus.STOPPED_REQUESTED: "Cancelled",
        RunStatus.STOPPED_BUDGET: "Failed",
    }
)


def _run_event_by_value(value: Any) -> RunEvent | None:
    if not isinstance(value, str):
        return None
    for event in RunEvent:
        if event.value == value:
            return event
    return None


def transition(
    status: RunStatus,
    event: RunEvent | str,
    guard_facts: Mapping[str, bool] | None = None,
) -> RunStatus:
    """The only writer of the run status: fire ``event`` on ``status``.

    Legal edges are RE1 to RE10; every other pair refuses with
    :class:`RunTransitionRefused` (code ``RUN_TRANSITION_REFUSED``). A guard
    names one fact the caller must assert in ``guard_facts``; a missing or
    false fact refuses with ``GuardFailed``.
    """
    facts = dict(guard_facts or {})
    if isinstance(event, str):
        fired = _run_event_by_value(event)
        if fired is None:
            raise RunTransitionRefused(
                from_status=status, event=event, reason="UnknownEvent"
            )
    elif not isinstance(event, RunEvent):
        raise RunTransitionRefused(
            from_status=status, event=event, reason="UnknownEvent"
        )
    else:
        fired = event
    if status.is_terminal:
        raise RunTransitionRefused(
            from_status=status, event=fired, reason="TerminalState"
        )
    for from_status, edge_event, target, guard in _RUN_EDGES:
        if from_status is status and edge_event is fired:
            break
    else:
        raise RunTransitionRefused(
            from_status=status, event=fired, reason="IllegalTransition"
        )
    if guard is not None and not facts.get(guard, False):
        raise RunTransitionRefused(
            from_status=status, event=fired, reason="GuardFailed"
        )
    return target


#: Fallback budgets when the host gives none.
DEFAULT_REQUEST_LIMIT = 50
DEFAULT_TIMEOUT_SECONDS = 600.0
DEFAULT_MAX_STEPS = 30
DEFAULT_RETRIES = 1


class RunPlanUnavailable(RuntimeError):
    """The run has no model: neither the host plan nor the caller named one."""


class RunPlanFailure(Exception):
    """Trusted host failure after measured work, before a plan can return.

    Raise this from the run decision provider only with actual reported usage.
    The run loop validates and snapshots the counters, then handles ``error``
    as the original failure or cancellation. This grants no execution and
    supplies no replacement budget. Model output and request metadata cannot
    set it. A provider without a usage ledger must raise its original error.
    """

    def __init__(
        self,
        error: BaseException,
        *,
        initial_usage: RunUsage,
        initial_elapsed_seconds: float = 0.0,
    ) -> None:
        if not isinstance(error, BaseException):
            raise TypeError("error must be an exception")
        super().__init__(str(error))
        self.error = error
        self.initial_usage = initial_usage
        self.initial_elapsed_seconds = initial_elapsed_seconds


@dataclass(frozen=True)
class RunBudget:
    """Limits the run loop enforces; it never widens them.

    ``candidate_slots`` is the candidate slot count of the stage
    allocation the run decision carries; the harness evolution cycle
    reads its candidate budget k from it and abstains below two.
    ``max_concurrent_requests`` caps concurrent model calls on dependency and
    recursive paths; waiting parents do not hold these permits.
    """

    request_limit: int | None = DEFAULT_REQUEST_LIMIT
    tool_calls_limit: int | None = None
    total_tokens_limit: int | None = None
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    candidate_slots: int | None = None
    candidate_slots_min: int | None = None
    max_attachment_bytes: int = 1_048_576
    max_attachments: int = 8
    max_concurrent_requests: int = 4
    image_token_cost: int = 1000
    history_token_limit: int | None = None


@dataclass(frozen=True)
class StopRule:
    """When the loop stops before the plan runs out.

    ``max_steps`` caps the plan steps executed. ``stop_markers`` stop the loop
    after a step whose output contains any marker.
    """

    max_steps: int = DEFAULT_MAX_STEPS
    stop_markers: tuple[str, ...] = ()


@dataclass(frozen=True)
class CodingConfig:
    """Coding-step settings the host receives for one run.

    Empty ``tasks`` and ``verifier_cases`` leave those steps off; the
    bounds apply once the host enables the step.
    """

    agent_names: tuple[str, ...] = ("claude", "opencode", "gemini")
    agent_capabilities: Mapping[str, Any] = field(default_factory=dict)
    preplan_command: str | None = None
    preplan_artifacts: tuple[str, ...] = ()
    tasks: Mapping[str, str] = field(default_factory=dict)
    verifier_cases: tuple[Mapping[str, Any], ...] = ()
    regression_command: str | None = None
    attack_policy_revision: str | None = None
    verifier_revision: str | None = None

    def __post_init__(self) -> None:
        if not 1 <= len(self.agent_names) <= 16:
            raise ValueError("agent_names must carry 1 to 16 names")
        if len(self.tasks) > 256:
            raise ValueError("tasks may carry at most 256 entries")
        if len(self.verifier_cases) > 64:
            raise ValueError("verifier_cases may carry at most 64 cases")


@dataclass(frozen=True)
class ProgramReuseConfig:
    """The program-reuse identity the host receives for one run."""

    task_signature: str
    program_graph: str


@dataclass(frozen=True)
class HarnessConfig:
    """In-run settings the host passes through to the agent framework."""

    retries: int = DEFAULT_RETRIES
    response_format: Literal["text", "json"] = "text"
    model_settings: Mapping[str, Any] = field(default_factory=dict)
    plan_scaffold: Literal["on", "off"] = "off"
    client_tools: tuple[str, ...] = ()
    client_tool_limits: ClientToolLimits | None = None
    consumers: tuple[str, ...] = ()
    coding: CodingConfig | None = None
    program_reuse: ProgramReuseConfig | None = None
    context_policy: str | None = None
    soft_threshold: float | None = None
    hard_threshold: float | None = None
    recent_window_fraction: float | None = None
    action_space: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ToolStep:
    """One host-bound call in a received dependency plan.

    The harness only carries the declared server, tool and JSON-shaped
    arguments. The host owns the caller, transport, credentials and policy.
    """

    server: str
    tool: str
    arguments: Mapping[str, Any] = field(default_factory=dict)


class ToolCallerUnavailable(RuntimeError):
    """A tool-step plan was received without a host-bound caller."""


class BindingUnresolved(RuntimeError):
    """A tool-step argument could not resolve from a completed prerequisite."""

    def __init__(
        self,
        task_id: str,
        argument: str,
        reference: str,
        reason: str,
    ) -> None:
        self.task_id = task_id
        self.argument = argument
        self.reference = reference
        self.reason = reason
        super().__init__(
            f"task {task_id!r} argument {argument!r}: {reason} ({reference})"
        )


class ToolCallRefused(RuntimeError):
    """The host-bound caller refused a declared tool step."""


class ToolCallFailed(RuntimeError):
    """The host-bound caller failed a declared tool step."""


@dataclass(frozen=True)
class RunRequest:
    """What the caller asks for. The host turns it into a :class:`RunPlan`."""

    task: str
    agent_name: str = ""
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    mode: str = "ask"
    context: str | None = None
    model_id: str | None = None
    allowed_tools: tuple[str, ...] | None = None
    max_steps: int | None = None
    budget_tokens: int | None = None
    response_format: Literal["text", "json"] = "text"
    metadata: Mapping[str, Any] = field(default_factory=dict)
    attachments: tuple[BinaryContent, ...] = ()
    ports: HostPorts = field(default_factory=HostPorts, compare=False, repr=False)
    replay_events: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class RunTask:
    """One received task and its direct prerequisites, not a planning decision."""

    task_id: str
    prompt: str = ""
    depends_on: tuple[str, ...] = ()
    call: ToolStep | None = None
    branch: Mapping[str, Any] | None = None
    join: Mapping[str, Any] | None = None
    decision: Mapping[str, Any] | None = None
    approval: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class DependencyPlan:
    """An admitted, finite DAG to execute with a bounded rolling ready queue.

    The host supplies declaration order, dependencies, the final output task
    and the concurrency limit. The harness neither adds tasks nor picks them.
    All tasks must contribute to ``output_task_id`` through dependency edges.
    """

    tasks: tuple[RunTask, ...]
    output_task_id: str
    max_concurrency: int = 1
    plan_id: str = ""


@dataclass(frozen=True)
class RunPlan:
    """Every decision of one run, as received from the host.

    ``model`` is a model id (resolved through the host model provider) or a
    ready pydantic-ai model. ``steps`` are the prompts run in order; each step
    sees the earlier steps' messages. ``toolsets`` are ready pydantic-ai
    toolsets; ``tool_names`` are host operations bound through the native
    toolset provider. ``dependency_plan`` opts into bounded rolling execution
    instead of serial ``steps``; the two are mutually exclusive.

    ``initial_usage`` and ``initial_elapsed_seconds`` are measured consumption
    supplied only by the trusted host, before execution (for example planning).
    Keep ``budget`` at its original bounds: the harness charges this consumption
    once. Model proposals and request metadata must never supply these fields.
    """

    model: Any
    execution_constraint: Literal["text-only"] | None = field(
        default=None, kw_only=True
    )
    instructions: str = ""
    steps: tuple[str, ...] = ()
    tool_names: tuple[str, ...] = ()
    toolsets: tuple[Any, ...] = ()
    budget: RunBudget = field(default_factory=RunBudget)
    stop: StopRule = field(default_factory=StopRule)
    harness: HarnessConfig = field(default_factory=HarnessConfig)
    client_transport: ClientCapabilityTransport | None = field(
        default=None, repr=False, compare=False
    )
    input_media_types: tuple[str, ...] = ()
    media_fallback: Literal["refuse", "text_placeholder"] = "refuse"
    dependency_plan: DependencyPlan | None = None
    initial_usage: RunUsage = field(default_factory=RunUsage)
    initial_elapsed_seconds: float = 0.0
    render_receipt: Any = field(default=None, repr=False, compare=False)
    """Optional ``RenderReceiptCheck`` the host supplies for an engine-rendered
    prompt; the loop validates it before the first model request."""
    study_sources: tuple[Mapping[str, Any], ...] = ()
    skill_transformation: Mapping[str, Any] | None = None
    lifecycle: RunLifecycle | None = None
    """Optional pre-configured run lifecycle the host carries; the loop
    builds the default one from ``harness.consumers`` without it."""


@dataclass
class GraphState:
    """Mutable state of one run while the loop executes it."""

    run_id: str
    query: str
    mode: str = "ask"
    status: RunStatus = RunStatus.PENDING
    step: int = 0
    outputs: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    error: str | None = None
    scaffold: PlanState | None = None
    metrics: RunMetrics = field(default_factory=RunMetrics)
    event_sequence: int = 0
    insights: tuple[Mapping[str, Any], ...] = ()
    """Insights observed during the run, as received; the plan-runtime
    consumer submits them at run end (R022.2)."""
    curriculum: Mapping[str, Any] | None = None
    """The task's subgoal hierarchy, checkpoints, trajectory and
    received depth for one curriculum submission (R028.1)."""
    compiled_context: str | None = None
    """The compiled fact prose the next model step receives (R036.2)."""
    media_fallbacks: int = 0
    """Images the received fallback replaced with a placeholder (R017.2)."""
    consumer_results: dict[str, Any] = field(default_factory=dict)
    """What admitted consumer steps received, by consumer name."""

    def add_usage(self, usage: Any) -> None:
        for key in ("requests", "tool_calls", "input_tokens", "output_tokens"):
            self.usage[key] = self.usage.get(key, 0) + int(getattr(usage, key, 0) or 0)
        self.usage["total_tokens"] = self.usage.get("input_tokens", 0) + self.usage.get(
            "output_tokens", 0
        )


@dataclass
class GraphDeps:
    """Dependencies the agent's tools see through ``RunContext.deps``."""

    state: GraphState
    plan: RunPlan
    config: Mapping[str, Any] = field(default_factory=dict)
    task_id: str | None = None
    dependency_outputs: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunResult:
    """Outcome of one run."""

    run_id: str
    output: str
    status: RunStatus
    steps: int
    usage: Mapping[str, int]
    error: str | None = None
    execution_mode: Literal["direct", "graph"] = "graph"
    metrics: Mapping[str, Any] = field(default_factory=dict)
    media_fallbacks: int = 0

    def as_dict(self) -> dict[str, Any]:
        """The public wire form; the status travels as its enum value."""
        return {
            "run_id": self.run_id,
            "output": self.output,
            "status": self.status.value,
            "steps": self.steps,
            "usage": dict(self.usage),
            "error": self.error,
            "execution_mode": self.execution_mode,
            "metrics": dict(self.metrics),
            **(
                {"media_fallbacks": self.media_fallbacks}
                if self.media_fallbacks
                else {}
            ),
        }


def _result_from_dict(data: Mapping[str, Any]) -> RunResult:
    """Rebuild a result from the public wire form of :meth:`RunResult.as_dict`."""
    status = data["status"]
    if not isinstance(status, str):
        raise ValueError("result status must be a string")
    try:
        status = RunStatus(status)
    except ValueError as exc:
        raise ValueError("unknown run status in result") from exc
    return RunResult(
        run_id=data["run_id"],
        output=data["output"],
        status=status,
        steps=data["steps"],
        usage=dict(data["usage"]),
        error=data["error"],
        execution_mode=data["execution_mode"],
        metrics=dict(data["metrics"]),
    )


def _validate_failure_outcome(result: RunResult) -> None:
    """Only a failed terminal state can travel through the exception port."""
    if (
        not isinstance(result.status, RunStatus)
        or result.status is not RunStatus.FAILED
    ):
        raise ValueError("result status must be failed")
    if type(result.steps) is not int or result.steps < 0:
        raise ValueError("result steps must be a nonnegative integer")
    if result.error is not None and type(result.error) is not str:
        raise ValueError("result error must be a string or None")
    if type(result.execution_mode) is not str or result.execution_mode not in (
        "direct",
        "graph",
    ):
        raise ValueError("result execution_mode must be direct or graph")


def _validate_public_usage(counters: Mapping[str, int]) -> None:
    """Accept a complete public ledger or preserve an unreported empty one."""
    if not counters:
        return
    if set(counters) != {
        "requests",
        "tool_calls",
        "input_tokens",
        "output_tokens",
        "total_tokens",
    }:
        raise ValueError("result usage must contain the five public counters")
    if any(type(value) is not int or value < 0 for value in counters.values()):
        raise ValueError("result usage counters must be nonnegative integers")
    if counters["total_tokens"] != counters["input_tokens"] + counters["output_tokens"]:
        raise ValueError("result usage total_tokens must match input and output")


def _failed_result_snapshot(result: RunResult) -> RunResult:
    """Copy before validation so the retained receipt cannot change afterward."""
    if not isinstance(result, RunResult):
        raise TypeError("result must be a RunResult")
    if not isinstance(result.usage, Mapping):
        raise ValueError("result usage must be a mapping")
    snapshot = RunResult(
        run_id=result.run_id,
        output=result.output,
        status=result.status,
        steps=result.steps,
        usage=MappingProxyType(dict(result.usage)),
        error=result.error,
        execution_mode=result.execution_mode,
        metrics=MappingProxyType(dict(result.metrics)),
    )
    if type(snapshot.run_id) is not str or not snapshot.run_id:
        raise ValueError("result run_id must be a nonempty string")
    if type(snapshot.output) is not str:
        raise ValueError("result output must be a string")
    _validate_failure_outcome(snapshot)
    _validate_public_usage(snapshot.usage)
    return snapshot


class RunFailed(RuntimeError):
    """A failed executor result with its authoritative aggregate receipt.

    The message and RuntimeError catch contract stay unchanged. The snapshot
    grants no execution or retry. Hosts must match its run identity before
    using its counters, and must not add them to the terminal ledger again.
    """

    def __init__(self, result: RunResult) -> None:
        snapshot = _failed_result_snapshot(result)
        super().__init__(snapshot.error or "agent run failed")
        self._result: RunResult = snapshot

    @property
    def result(self) -> RunResult:
        return self._result

    @property
    def run_id(self) -> str:
        return self._result.run_id

    @property
    def usage(self) -> Mapping[str, int]:
        return self._result.usage

    def __reduce__(self) -> tuple[type[RunFailed], tuple[RunResult], dict[str, Any]]:
        # RuntimeError reconstructs from its message by default. Rebuild this
        # typed carrier from plain scalar data instead of a mapping proxy.
        state = {key: value for key, value in self.__dict__.items() if key != "_result"}
        return type(self), (_result_from_dict(self.result.as_dict()),), state


def default_run_plan(request: RunRequest, config: Mapping[str, Any]) -> RunPlan:
    """The fallback plan when no host decision provider is registered.

    * model: ``request.model_id``, else ``config["agent_model"]``; none fails closed;
    * instructions: the named specialist's prompt, else ``config["system_prompt"]``;
    * steps: the task alone, prefixed by ``request.context`` when given;
    * tools: only ``request.allowed_tools`` (bound through the host) plus the
      toolsets the graph was built with; nothing is discovered;
    * budget, stop rule and harness: the module defaults above, narrowed by
      ``request.max_steps`` and ``request.budget_tokens``.
    """
    model = request.model_id or config.get("agent_model")
    if not model:
        raise RunPlanUnavailable(
            "no model: register a run decision provider or name agent_model"
        )
    specialists = config.get("specialists") or {}
    instructions = str(
        specialists.get(request.agent_name) or config.get("system_prompt") or ""
    )
    prompt = request.task
    if request.context:
        prompt = f"{request.context}\n\n{request.task}"
    return RunPlan(
        model=model,
        instructions=instructions,
        steps=(prompt,),
        tool_names=tuple(request.allowed_tools or ()),
        toolsets=tuple(config.get("toolsets") or ()),
        budget=RunBudget(total_tokens_limit=request.budget_tokens),
        stop=StopRule(max_steps=request.max_steps or DEFAULT_MAX_STEPS),
        harness=HarnessConfig(response_format=request.response_format),
    )


__all__ = [
    "BindingUnresolved",
    "CodingConfig",
    "DependencyPlan",
    "GraphDeps",
    "GraphState",
    "HarnessConfig",
    "ProgramReuseConfig",
    "RUN_TERMINAL_VOCAB",
    "RUN_TRANSITION_REFUSED",
    "RefusalReason",
    "RunBudget",
    "RunEvent",
    "RunFailed",
    "RunPlan",
    "RunPlanUnavailable",
    "RunRequest",
    "RunResult",
    "RunStatus",
    "RunTask",
    "RunTransitionRefused",
    "StopRule",
    "TASK_TRANSITION_REFUSED",
    "TaskEvent",
    "TaskStatus",
    "TaskTransitionRefused",
    "ToolCallerUnavailable",
    "ToolCallFailed",
    "ToolCallRefused",
    "ToolStep",
    "default_run_plan",
    "transition",
]
