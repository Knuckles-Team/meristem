"""In-run reservation of received aggregate limits across parallel tasks."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Iterator, Mapping
from contextlib import nullcontext
from dataclasses import dataclass, replace
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, WrapModelRequestHandler
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import RunUsage, UsageLimits

from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import (
    EngineAnswer,
    EngineDecisionPort,
    EngineDecisionRefused,
)

from .state import GraphState, RunBudget, RunPlan, RunPlanFailure, RunRequest


def initial_run_usage(plan: RunPlan | RunPlanFailure) -> RunUsage:
    """Snapshot only the public counters; never estimate unreported usage."""
    usage = plan.initial_usage
    if not isinstance(usage, RunUsage):
        raise ValueError("initial_usage must be RunUsage")
    counters = {
        key: getattr(usage, key)
        for key in ("requests", "tool_calls", "input_tokens", "output_tokens")
    }
    if any(type(value) is not int or value < 0 for value in counters.values()):
        raise ValueError("initial_usage counters must be nonnegative integers")
    if usage.total_tokens != counters["input_tokens"] + counters["output_tokens"]:
        raise ValueError("initial_usage total_tokens must match input and output")
    elapsed = plan.initial_elapsed_seconds
    if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0:
        raise ValueError("initial_elapsed_seconds must be finite and nonnegative")
    return RunUsage(**counters)


async def _request_drained(
    handler: WrapModelRequestHandler, request_context: ModelRequestContext
) -> ModelResponse:
    """Deliver cancellation once despite the framework's level cancel scopes."""

    async def call() -> ModelResponse:
        return await handler(request_context)

    request = asyncio.create_task(call())
    interrupted: BaseException | None = None
    response: ModelResponse | None = None
    try:
        response = await asyncio.shield(request)
    except BaseException as exc:
        interrupted = exc
    finally:
        if not request.done():
            request.cancel()
        drain = asyncio.gather(request, return_exceptions=True)
        while not drain.done():
            try:
                await asyncio.shield(drain)
            except asyncio.CancelledError as exc:
                interrupted = interrupted or exc
    if interrupted is not None:
        raise interrupted
    assert response is not None
    return response


class SharedBudget(AbstractCapability[Any]):
    """Reserve before dispatch, never reset the budget for a child or retry.

    Reservations have no await between their check and increment. They are
    atomic within the run's event loop. A failed/cancelled attempt still consumes
    its reservation. An unspecified tool limit uses a finite fallback of 50.
    Token accounting uses reported responses, as in the serial
    loop: unknown in-flight token usage cannot be predicted or pre-reserved.
    """

    def __init__(
        self,
        budget: RunBudget,
        *,
        max_concurrent_requests: int | None = None,
        initial_usage: RunUsage | None = None,
    ) -> None:
        self.usage = RunUsage()
        if initial_usage is not None:
            self.usage.incr(initial_usage)
        self._has_initial_usage = initial_usage is not None
        self.limits = UsageLimits(
            request_limit=budget.request_limit,
            tool_calls_limit=50
            if budget.tool_calls_limit is None
            else budget.tool_calls_limit,
            total_tokens_limit=budget.total_tokens_limit,
        )
        self.slots = (
            asyncio.Semaphore(max_concurrent_requests)
            if max_concurrent_requests is not None
            else None
        )

    def check_initial_consumption(self) -> None:
        """Refuse an exhausted host ledger before constructing any child."""
        self.limits.check_before_request(self.usage)
        token_limit = self.limits.total_tokens_limit
        if token_limit is not None and self.usage.total_tokens >= token_limit:
            raise UsageLimitExceeded(
                f"Initial usage exhausts the total_tokens_limit of {token_limit}"
            )
        # An exact tool cap still allows a model-only answer. Pydantic AI's
        # generic check treats zero as an unspecified limit, so enforce the
        # received bound explicitly for both prior and new host calls.
        limit = self.limits.tool_calls_limit
        if limit is not None and self.usage.tool_calls > limit:
            raise UsageLimitExceeded(
                f"Initial usage exceeds the tool_calls_limit of {limit}"
            )

    async def wrap_model_request(
        self,
        ctx: RunContext[Any],
        *,
        request_context: ModelRequestContext,
        handler: WrapModelRequestHandler,
    ) -> ModelResponse:
        return await self._wrap_model_request(ctx, request_context, handler, None)

    async def _wrap_model_request(
        self,
        ctx: RunContext[Any],
        request_context: ModelRequestContext,
        handler: WrapModelRequestHandler,
        local_usage: RunUsage | None,
    ) -> ModelResponse:
        # Only a model request holds a permit, never a parent waiting on its
        # child tools. Nested recursive runs cannot deadlock the request pool.
        async with self.slots if self.slots is not None else nullcontext():
            self._ensure_live()
            self.limits.check_before_request(self.usage)
            token_limit = self.limits.total_tokens_limit
            if (
                self._has_initial_usage
                and token_limit is not None
                and self.usage.total_tokens >= token_limit
            ):
                raise UsageLimitExceeded(
                    f"The next request would exceed the total_tokens_limit of {token_limit}"
                )
            self.usage.requests += 1
            if local_usage is not None:
                local_usage.requests += 1
            before_input = ctx.usage.input_tokens
            before_output = ctx.usage.output_tokens
            response: ModelResponse | None = None
            try:
                response = await _request_drained(handler, request_context)
            finally:
                # The framework records provider usage before response hooks,
                # including billed fallback attempts and hook-induced retries.
                # Local node usage avoids counting another child's work twice.
                input_delta = ctx.usage.input_tokens - before_input
                output_delta = ctx.usage.output_tokens - before_output
                # FunctionModel and a few host adapters return RequestUsage
                # without mutating RunContext. Preserve their reported usage
                # while keeping the context delta authoritative when present.
                if response is not None:
                    if input_delta == 0 and type(response.usage.input_tokens) is int:
                        input_delta = response.usage.input_tokens
                    if output_delta == 0 and type(response.usage.output_tokens) is int:
                        output_delta = response.usage.output_tokens
                self.usage.input_tokens += input_delta
                self.usage.output_tokens += output_delta
                if local_usage is not None:
                    local_usage.input_tokens += input_delta
                    local_usage.output_tokens += output_delta
            self.limits.check_tokens(self.usage)
            assert response is not None
            return response

    async def before_tool_execute(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        self._reserve_tool_call(None)
        return args

    def reserve_tool_call(self) -> None:
        """Reserve one host-bound dependency call before it can have an effect."""
        self._reserve_tool_call(None)

    def _reserve_tool_call(self, local_usage: RunUsage | None) -> None:
        self._ensure_live()
        limit = self.limits.tool_calls_limit
        if limit is not None and self.usage.tool_calls >= limit:
            raise UsageLimitExceeded(
                f"The next tool call would exceed the tool_calls_limit of {limit}"
            )
        self.usage.tool_calls += 1
        if local_usage is not None:
            local_usage.tool_calls += 1

    def _ensure_live(self) -> None:
        """Ordinary run budgets remain valid for their owning run."""


class LiveSharedBudget(SharedBudget):
    """One host-held, in-process authority shared by concurrent runs.

    This object is neither serializable authority nor a durable spending
    ledger. The host must only pass it to runs it admitted under the same
    execution, and close it when that authority ends. It cannot survive a
    process restart or cross an event loop.
    """

    def __init__(self, budget: RunBudget, *, deadline: float) -> None:
        loop = asyncio.get_running_loop()
        if (
            not math.isfinite(deadline)
            or deadline > loop.time() + budget.timeout_seconds
        ):
            raise ValueError("shared budget deadline is invalid")
        # Providers report tokens only after a response. Serialize requests
        # under a finite token limit so overlapping calls cannot each observe
        # the same remaining allowance before either reports usage.
        slots = (
            1
            if budget.total_tokens_limit is not None
            else budget.max_concurrent_requests
        )
        super().__init__(budget, max_concurrent_requests=slots)
        # Enforce the token ceiling at equality even when this authority began
        # with no recorded usage.
        self._has_initial_usage = True
        self.budget = budget
        self.deadline = deadline
        self._loop = loop
        self._active = True

    def _ensure_live(self) -> None:
        if not self._active or asyncio.get_running_loop() is not self._loop:
            raise RuntimeError("shared execution budget is not live")
        if self._loop.time() >= self.deadline:
            raise TimeoutError("shared execution budget expired")

    def require(self, budget: RunBudget) -> None:
        self._ensure_live()
        if budget != self.budget:
            raise ValueError("shared execution budget does not match the received plan")
        self.check_initial_consumption()

    def run_view(self) -> SharedBudget:
        """Create a run-local accounting view over this same live authority."""
        self._ensure_live()
        return _SharedBudgetView(self)

    def close(self) -> None:
        """Prevent new effects; the host remains responsible for draining in-flight work."""
        self._active = False


class _SharedBudgetView(SharedBudget):
    """Per-run counters; all reservations still debit the same live owner."""

    def __init__(self, owner: LiveSharedBudget) -> None:
        self._owner = owner
        self.usage = RunUsage()
        self.limits = owner.limits
        self.slots = owner.slots

    async def wrap_model_request(
        self,
        ctx: RunContext[Any],
        *,
        request_context: ModelRequestContext,
        handler: WrapModelRequestHandler,
    ) -> ModelResponse:
        return await self._owner._wrap_model_request(
            ctx, request_context, handler, self.usage
        )

    async def before_tool_execute(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        self._owner._reserve_tool_call(self.usage)
        return args

    def reserve_tool_call(self) -> None:
        self._owner._reserve_tool_call(self.usage)


# ---------------------------------------------------------------------------
# Received-value consumers of the engine decision port. Each function applies
# one received decision: it holds no threshold of its own, refuses with a
# closed code and returns the applied value with its record digest. An
# unbound port refuses ``engine_decision_unavailable``; engine refusal codes
# pass through unchanged; an abstained answer decides nothing.


def _refuse(answer: EngineAnswer) -> None:
    """Pass a closed engine refusal through, unchanged."""
    if answer.refusal_code is not None:
        raise ConsumerRefused(answer.refusal_code)


def _is_decision(answer: EngineAnswer) -> bool:
    """Whether the engine answered with a decision the harness may apply."""
    return answer.refusal_code is None and answer.outcome in ("solved", "committed")


def _positive_int(value: Any, name: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _nonnegative_int(value: Any, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


@dataclass(frozen=True, slots=True)
class AdmittedTopology:
    """The width, depth and rounds one re-admission answer admits.

    The engine's EnforcementRecord caps are the ceiling: the run widens
    only to them, never on a refusal. ``record_digest`` is the
    EnforcementRecord digest the run trace carries.
    """

    width: int
    depth: int
    rounds: int
    record_digest: str


def _enforcement_caps(answer: EngineAnswer) -> dict[str, int]:
    """The admitted caps of the EnforcementRecord the answer carries."""
    caps: dict[str, int] = {}
    for knob in ("max_width", "max_depth", "max_rounds"):
        value = answer.payload.get(knob)
        if type(value) is not int or value < 1:
            raise ConsumerRefused("admission_unanswered")
        caps[knob] = value
    return caps


async def request_wider_admission(
    state: GraphState, width: int, depth: int, port: EngineDecisionPort | None
) -> AdmittedTopology:
    """Re-admit a wider width or depth with one new topology question.

    The question asks the engine for the wanted width and depth and waits
    for the answer; the admitted EnforcementRecord caps are the only
    admission the run may widen to. Engine refusal codes
    (``TOPOLOGY_WIDTH_EXCEEDS_CAP``, ``TOPOLOGY_DEPTH_EXCEEDS_CAP``,
    ``TOPOLOGY_ROUNDS_EXCEEDS_CAP``, ``TOPOLOGY_TOKENS_EXCEEDS_CAP``) pass
    through; an answer without an admitted record refuses
    ``admission_unanswered``; an unbound port refuses
    ``engine_decision_unavailable`` and the guarded action does not start.
    The knobs are received values: ``topology.max_width``,
    ``topology.max_depth`` and ``topology.max_rounds``.
    """
    if port is None:
        raise EngineDecisionRefused("engine_decision_unavailable")
    _positive_int(width, "width")
    _positive_int(depth, "depth")
    answer = await port.assemble(
        {
            "kind": "topology_admission",
            "run_id": state.run_id,
            "width": width,
            "depth": depth,
        },
        run_ref=state.run_id,
    )
    _refuse(answer)
    if not _is_decision(answer):
        raise ConsumerRefused("admission_unanswered")
    caps = _enforcement_caps(answer)
    if answer.record_digest is None:
        raise ConsumerRefused("admission_unanswered")
    return AdmittedTopology(
        width=min(width, caps["max_width"]),
        depth=min(depth, caps["max_depth"]),
        rounds=caps["max_rounds"],
        record_digest=answer.record_digest,
    )


def _topology_values(answer: EngineAnswer) -> tuple[int, int, int]:
    """The received width, depth and rounds of an applied topology answer."""
    width = answer.payload.get("width")
    depth = answer.payload.get("depth")
    rounds = answer.payload.get("rounds")
    if any(type(value) is not int or value < 1 for value in (width, depth, rounds)):
        raise ConsumerRefused("topology_not_applicable")
    return width, depth, rounds


async def apply_topology(
    request: RunRequest, plan: RunPlan, port: EngineDecisionPort | None
) -> RunPlan:
    """Ask the engine once for the run topology and apply it to the plan.

    One ``topology`` decision carries the received width, depth and
    rounds; the harness keeps no success-rate estimate and no outcome
    average. Width applies to ``RunBudget.max_concurrent_requests``,
    depth to the dependency plan's ``max_concurrency`` and rounds to the
    stop rule's ``max_steps``. An answer above the host ceiling in the
    plan, or one without an applicable topology, refuses
    ``topology_not_applicable``; engine codes pass through; an unbound
    port refuses ``engine_decision_unavailable`` and decides nothing.
    """
    if port is None:
        raise EngineDecisionRefused("engine_decision_unavailable")
    answer = await port.decide(
        "topology",
        {
            "task_class": request.metadata.get("task_class"),
            "capacity": plan.budget.max_concurrent_requests,
        },
        run_ref=request.run_id,
    )
    _refuse(answer)
    if not _is_decision(answer):
        return plan
    width, depth, rounds = _topology_values(answer)
    if width > plan.budget.max_concurrent_requests:
        raise ConsumerRefused("topology_not_applicable")
    updated = replace(plan, budget=replace(plan.budget, max_concurrent_requests=width))
    if plan.dependency_plan is not None:
        updated = replace(
            updated,
            dependency_plan=replace(plan.dependency_plan, max_concurrency=depth),
        )
    return replace(updated, stop=replace(plan.stop, max_steps=rounds))


def _run_adjustment_options(
    answer: EngineAnswer,
) -> tuple[str | None, int | None, int | None]:
    """The received model, width and budget_tokens options of the answer."""
    model: str | None = None
    width: int | None = None
    tokens: int | None = None
    for option in answer.option_ids:
        if type(option) is not str or ":" not in option:
            raise ValueError("run adjustment options are closed name:value strings")
        name, _, value = option.partition(":")
        if name == "model":
            if model is not None or not value:
                raise ValueError("the model option needs exactly one model id")
            model = value
        elif name == "width":
            if width is not None:
                raise ValueError("one width option per run adjustment answer")
            width = _positive_int(int(value), "width")
        elif name == "budget_tokens":
            if tokens is not None:
                raise ValueError("one budget_tokens option per run adjustment answer")
            tokens = _nonnegative_int(int(value), "budget_tokens")
        else:
            raise ValueError(f"unknown run adjustment option: {name}")
    return model, width, tokens


async def apply_run_adjustment(
    request: RunRequest, plan: RunPlan, port: EngineDecisionPort | None
) -> RunPlan:
    """Adjust the next run from observed cost through one decision.

    One ``run_adjustment`` decision is asked with the child and operation
    the run continues, the current model and width, the host width
    ceiling and the token budget. The received options ``model:<id>``,
    ``width:<n>`` and ``budget_tokens:<n>`` apply within the host
    ceiling; a wider answer refuses ``width_above_host_ceiling``. Engine
    codes pass through; an unbound port refuses
    ``engine_decision_unavailable`` and decides nothing. The knobs are
    received values: ``width.error_hi``, ``width.error_lo`` and
    ``width.window``.
    """
    if port is None:
        raise EngineDecisionRefused("engine_decision_unavailable")
    received = request.metadata.get("host_width_ceiling")
    ceiling = (
        _positive_int(received, "host_width_ceiling")
        if received is not None
        else plan.budget.max_concurrent_requests
    )
    answer = await port.decide(
        "run_adjustment",
        {
            "child_id": request.metadata.get("child_id"),
            "operation": request.metadata.get("operation"),
            "current_model": plan.model if type(plan.model) is str else None,
            "current_width": plan.budget.max_concurrent_requests,
            "host_width_ceiling": ceiling,
            "budget_tokens": plan.budget.total_tokens_limit,
        },
        run_ref=request.run_id,
    )
    _refuse(answer)
    if not _is_decision(answer):
        return plan
    model, width, tokens = _run_adjustment_options(answer)
    if width is not None and width > ceiling:
        raise ConsumerRefused("width_above_host_ceiling")
    budget = plan.budget
    if width is not None:
        budget = replace(budget, max_concurrent_requests=width)
    if tokens is not None:
        budget = replace(budget, total_tokens_limit=tokens)
    if model is None:
        return replace(plan, budget=budget)
    return replace(plan, model=model, budget=budget)


@dataclass(frozen=True, slots=True)
class DelegationAdmission:
    """One fresh delegation verdict the engine served.

    The verdict is ``admit``; ``record_digest`` is the verdict record the
    run trace carries. A refused or abstained verdict never returns
    here; it refuses ``delegation_not_admitted``.
    """

    task_class: str
    record_digest: str


async def require_delegation_admission(
    task_class: str, port: EngineDecisionPort | None, *, run_ref: str
) -> DelegationAdmission:
    """Ask the engine for a fresh delegation verdict before one delegation.

    The verdict is asked fresh before each delegation, so no cached
    verdict is ever used. Only an ``admit`` verdict starts the
    delegation; a refused or abstained verdict refuses
    ``delegation_not_admitted``; engine codes pass through; an unbound
    port refuses ``engine_decision_unavailable`` and the guarded action
    does not start. The knobs are received values:
    ``delegation.min_samples``, ``delegation.max_age`` and
    ``delegation.quality_threshold``.
    """
    if type(task_class) is not str or not task_class:
        raise ValueError("a delegation admission needs a task class")
    if port is None:
        raise EngineDecisionRefused("engine_decision_unavailable")
    answer = await port.decide(
        "delegation_admission",
        {
            "task_class": task_class,
            "metric_id": None,
            "threshold_q32": None,
            "window_start_ms": None,
            "window_end_ms": None,
        },
        run_ref=run_ref,
    )
    _refuse(answer)
    verdict = answer.payload.get("verdict") if _is_decision(answer) else "abstain"
    if verdict != "admit":
        raise ConsumerRefused("delegation_not_admitted")
    if answer.record_digest is None:
        raise ValueError("the admitted verdict carries no record digest")
    return DelegationAdmission(
        task_class=task_class, record_digest=answer.record_digest
    )


COMPARATIVE_STAGES: tuple[str, ...] = ("synthesis", "extraction")
"""The stages whose model calls a comparative plan admits; discovery issues none."""


def _comparative_entries(
    plan: RunPlan,
) -> list[tuple[str, str, Mapping[str, Any], int]]:
    """The per-stage call budgets the plan admits, in plan order.

    The plan candidates are its received study sources; each carries its
    ``candidate_id`` and its per-stage call budgets. A call for a
    candidate outside the plan cannot be built.
    """
    entries: list[tuple[str, str, Mapping[str, Any], int]] = []
    for source in plan.study_sources:
        if not isinstance(source, Mapping):
            raise ValueError("comparative plan candidates are received records")
        candidate_id = source.get("candidate_id")
        if type(candidate_id) is not str or not candidate_id:
            raise ValueError("a comparative plan candidate needs its id")
        stages = source.get("stages")
        if not isinstance(stages, Mapping) or set(stages) != set(COMPARATIVE_STAGES):
            raise ValueError(
                "comparative plan candidates budget synthesis and extraction only"
            )
        for stage in COMPARATIVE_STAGES:
            entries.append(
                (
                    stage,
                    candidate_id,
                    source,
                    _nonnegative_int(stages[stage], f"{stage} calls"),
                )
            )
    return entries


def comparative_calls(plan: RunPlan) -> Iterator[tuple[str, str, Mapping[str, Any]]]:
    """Yield the model calls one comparative-analysis plan admits.

    Each entry is ``(stage, candidate_id, batch)``: the stage, the plan
    candidate it may target and the candidate record the call works
    from. Synthesis and extraction model calls come only from this
    generator; discovery issues no model call. A stage stops at its call
    budget and the run stops at the plan's total ceiling, the received
    ``RunBudget.request_limit``. Stage budgets beyond the total ceiling
    refuse ``comparative_budget_exhausted``. The knobs are received
    values: ``comparative.synthesis_importance_min``,
    ``comparative.extraction_importance_min``, ``comparative.synthesis_batch``
    and ``comparative.total_call_ceiling_max``.
    """
    entries = _comparative_entries(plan)
    ceiling = plan.budget.request_limit
    if ceiling is not None:
        _nonnegative_int(ceiling, "request_limit")
        if sum(count for *_, count in entries) > ceiling:
            raise ConsumerRefused("comparative_budget_exhausted")
    issued = 0
    for stage, candidate_id, batch, count in entries:
        for _ in range(count):
            if ceiling is not None and issued >= ceiling:
                return
            issued += 1
            yield (stage, candidate_id, batch)


@dataclass(frozen=True, slots=True)
class RetrievalCostReport:
    """The cost report one served retrieval response carries.

    The engine clamps the tenant ceiling; the harness reads the reported
    allowance and never retries with a larger request.
    """

    cost_units: int
    remaining_units: int
    rows: int
    visits: int

    @property
    def exhausted(self) -> bool:
        """Whether the reported remaining budget is exhausted."""
        return self.remaining_units <= 0


def stop_on_exhausted_retrieval(response: EngineAnswer) -> RetrievalCostReport:
    """Read the cost report of one served retrieval response.

    Each ``IndexlessRetrieve`` response carries the cost report; when the
    remaining budget is exhausted the loop stops, refusing
    ``retrieval_budget_exhausted``. It never retries with a larger
    request. Engine refusal codes pass through; a response without the
    cost report is a harness contract failure. The knobs are received
    values: ``indexless.max_cost_units``, ``indexless.max_rows`` and
    ``indexless.max_visits``.
    """
    _refuse(response)
    report = response.payload.get("cost_report")
    if not isinstance(report, Mapping):
        raise ValueError("the retrieval response lacks the cost report")
    cost = RetrievalCostReport(
        cost_units=_nonnegative_int(report.get("cost_units"), "cost_units"),
        remaining_units=_nonnegative_int(
            report.get("remaining_units"), "remaining_units"
        ),
        rows=_nonnegative_int(report.get("rows"), "rows"),
        visits=_nonnegative_int(report.get("visits"), "visits"),
    )
    if cost.exhausted:
        raise ConsumerRefused("retrieval_budget_exhausted")
    return cost


@dataclass(frozen=True, slots=True)
class ObservationWindow:
    """One run's observation window, recorded once at run start.

    Unknown values are sent as unknown: an unset limit stays ``None`` in
    the payload. The window holds the received budget bounds and the
    initial usage snapshot of the run.
    """

    run_id: str
    request_limit: int | None
    tool_calls_limit: int | None
    total_tokens_limit: int | None
    max_concurrent_requests: int
    timeout_seconds: float
    requests: int
    tool_calls: int
    input_tokens: int
    output_tokens: int

    def payload(self) -> dict[str, Any]:
        """The window as sent: unknown values as unknown."""
        return {
            "run_id": self.run_id,
            "request_limit": self.request_limit,
            "tool_calls_limit": self.tool_calls_limit,
            "total_tokens_limit": self.total_tokens_limit,
            "max_concurrent_requests": self.max_concurrent_requests,
            "timeout_seconds": self.timeout_seconds,
            "requests": self.requests,
            "tool_calls": self.tool_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


def _width_option(answer: EngineAnswer) -> int | None:
    """The received width option of an applied answer, if any."""
    widths = [
        option.split(":", 1)[1]
        for option in answer.option_ids
        if type(option) is str and option.startswith("width:")
    ]
    if len(widths) > 1:
        raise ValueError("one width option per run adjustment answer")
    if not widths:
        return None
    return _positive_int(int(widths[0]), "width")


async def send_observation_window(
    run_id: str,
    budget: RunBudget,
    usage: RunUsage,
    port: EngineDecisionPort | None,
) -> RunBudget:
    """Record one run observation window, send it and apply its width.

    The window is one record per run, built from the run's received
    budget and its initial usage snapshot; unknown values are sent as
    unknown. One ``run_adjustment`` decision carries it to the engine.
    A received narrower width applies at once; a wider width mid-run
    refuses ``width_widening_refused``. Engine codes pass through; an
    unbound port refuses ``engine_decision_unavailable``.
    """
    if port is None:
        raise EngineDecisionRefused("engine_decision_unavailable")
    window = ObservationWindow(
        run_id=run_id,
        request_limit=budget.request_limit,
        tool_calls_limit=budget.tool_calls_limit,
        total_tokens_limit=budget.total_tokens_limit,
        max_concurrent_requests=budget.max_concurrent_requests,
        timeout_seconds=budget.timeout_seconds,
        requests=usage.requests,
        tool_calls=usage.tool_calls,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
    )
    answer = await port.decide("run_adjustment", window.payload(), run_ref=run_id)
    _refuse(answer)
    if not _is_decision(answer):
        return budget
    width = _width_option(answer)
    if width is None:
        return budget
    if width < budget.max_concurrent_requests:
        return replace(budget, max_concurrent_requests=width)
    if width > budget.max_concurrent_requests:
        raise ConsumerRefused("width_widening_refused")
    return budget
