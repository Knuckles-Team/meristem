"""The recursive run loop.

The root agent never receives the whole input. It sees the input length and a
preview, reads slices through ``read_input``, and hands bounded sub-questions
to ``sub_query``, which runs a child agent one level deeper. The loop stops
recursing at ``max_depth`` and enforces the request and token budgets it is
given on every level. Model, depth and budgets come from :class:`RLMConfig`;
when the config names no model, the host run plan for agent ``"rlm"`` supplies
the model and budget (see :func:`meristem.graph.resolve_plan`).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from pydantic_ai import Agent, RunContext
from pydantic_ai.usage import UsageLimits

from meristem.graph.budget import SharedBudget
from meristem.graph.executor import resolve_plan
from meristem.graph.live_events import (
    LineageBinding,
    LineageIdentityError,
    emit_lineage_event,
    lineage_relationship,
)
from meristem.graph.state import RunBudget, RunEvent, RunRequest, RunStatus, transition
from meristem.harness.conversation_history import read_recursive_run_history
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineDecisionRefused, HostPorts

from .research_steps import next_read
from .span_reads import read_span
from .telemetry import SandboxFatalError, classify_failure

ROOT_INSTRUCTIONS = (
    "Answer the task. The input is not in this prompt: read it with "
    "read_input(start, end) and delegate focused questions over a slice with "
    "sub_query(question, start, end)."
)


@dataclass(frozen=True)
class RLMConfig:
    """Limits and models of one recursive run, as received from the host."""

    model: Any = None
    sub_model: Any = None
    max_depth: int = 3
    preview_chars: int = 500
    max_slice_chars: int = 20_000
    request_limit: int = 50
    total_tokens_limit: int | None = None
    timeout_seconds: float = 600.0
    max_concurrent_requests: int = 4
    tool_calls_limit: int | None = 50


@dataclass
class _Env:
    text: str
    depth: int
    config: RLMConfig
    budget: SharedBudget
    ports: HostPorts
    lineage: LineageBinding | None = None
    child_identity_provider: (
        Callable[[LineageBinding, str], LineageBinding | None] | None
    ) = None
    event_sequence: int = 0
    tool_call_ref: str = ""
    """The tool of the last result the recursion-mode signals carry."""
    recursion_mode: str | None = None
    """The last engine-decided mode, direct or recursive, or None."""
    sub_call_signature: Mapping[str, Any] | None = None
    """The host-provided sub-call signature; admission is signed-only."""
    document_id: str | None = None
    """The document one document-reference run reads through ReadSpan."""
    document_revision: str = "1"
    """The document revision the run reads."""
    toc_digest: str | None = None
    """The received toc digest ReadSpan reads travel with."""


def _emit_lineage(
    env: _Env,
    kind: str,
    *,
    relationships: tuple[dict[str, Any], ...] = (),
    **fields: Any,
) -> dict[str, Any] | None:
    """Publish one event only when the host supplied an explicit identity."""

    if env.lineage is None:
        return None
    env.event_sequence += 1
    return emit_lineage_event(
        kind,
        env.lineage,
        env.event_sequence,
        ports=env.ports,
        relationships=relationships,
        **fields,
    )


def _model(ports: HostPorts, value: Any) -> Any:
    return ports.resolve_model(value) if isinstance(value, str) else value


def _run_ref(env: _Env) -> str:
    """The run identity one consumer decision travels with."""
    return env.lineage.run_id if env.lineage is not None else "rlm"


def _canonical_digest(value: object) -> str:
    """The canonical JSON SHA-256 digest of one received value."""
    canonical = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _model_call(model: Any, prompt: str) -> str:
    """One tool-less model call: the leaf and merge steps of a split."""
    result = await Agent(model, output_type=str).run(prompt)
    return str(result.output)


def _agent(
    config: RLMConfig,
    depth: int,
    output_type: Any,
    budget: SharedBudget,
    ports: HostPorts,
) -> Agent[_Env, Any]:
    model = _model(
        ports, config.model if depth == 0 else (config.sub_model or config.model)
    )
    agent: Agent[_Env, Any] = Agent(
        model,
        output_type=output_type,
        deps_type=_Env,
        instructions=ROOT_INSTRUCTIONS,
        capabilities=[budget],
    )

    @agent.tool
    async def read_input(ctx: RunContext[_Env], start: int = 0, end: int = 0) -> str:
        """Return input[start:end] (capped at the slice limit)."""
        ctx.deps.tool_call_ref = "read_input"
        stop = end or start + ctx.deps.config.max_slice_chars
        stop = min(stop, start + ctx.deps.config.max_slice_chars)
        ports = ctx.deps.ports
        bound = ports.engine_decision_port() is not None
        if bound:
            step = await next_read(ctx.deps)
            if step.action == "stop":
                out = f"research ended: {step.reason}"
            elif ctx.deps.document_id is not None:
                out = await read_span(
                    ctx.deps,
                    ctx.deps.document_id,
                    ctx.deps.document_revision,
                    start,
                    stop,
                )
            else:
                out = ctx.deps.text[start:stop]
        elif ctx.deps.document_id is not None:
            out = await read_span(
                ctx.deps, ctx.deps.document_id, ctx.deps.document_revision, start, stop
            )
        else:
            out = ctx.deps.text[start:stop]
        if bound:
            await choose_mode(ctx.deps, out, ports)
        return out

    @agent.tool
    async def sub_query(
        ctx: RunContext[_Env], question: str, start: int = 0, end: int = 0
    ) -> str:
        """Answer ``question`` over input[start:end] with a child run."""
        ctx.deps.tool_call_ref = "sub_query"
        if ctx.deps.depth + 1 >= ctx.deps.config.max_depth:
            return "refused: maximum recursion depth reached"
        if (
            ctx.deps.sub_call_signature is not None
            and ctx.deps.ports.engine_decision_port() is not None
        ):
            request = SubCallRequest(
                signature_digest=_canonical_digest(ctx.deps.sub_call_signature),
                model_digest=_canonical_digest(
                    str(ctx.deps.config.sub_model or ctx.deps.config.model or "rlm")
                ),
                tool_digest=hashlib.sha256(b"sub_query").hexdigest(),
                parent_ref=_run_ref(ctx.deps),
            )
            admission = await admit_sub_call(
                request, ctx.deps.ports, run_ref=request.parent_ref
            )
            if not admission.admitted:
                return "refused: sub-call not admitted by the engine"
        if ctx.deps.recursion_mode == "direct":
            # The engine decided the sub-question runs direct: one call on
            # the host's rlm model over the slice, no child run.
            model = ctx.deps.ports.resolve_model("rlm")
            out = await _model_call(
                model,
                f"{question}\n\n[slice {start}:{end or 'end'}]\n"
                f"{ctx.deps.text[start : end or None]}",
            )
        else:
            child_lineage: LineageBinding | None = None
            if ctx.deps.lineage is not None:
                if ctx.deps.child_identity_provider is None:
                    raise LineageIdentityError(
                        "lineage-bound child requires an admitted identity provider"
                    )
                child_lineage = ctx.deps.child_identity_provider(
                    ctx.deps.lineage, question
                )
                if child_lineage is None:
                    raise LineageIdentityError(
                        "lineage-bound child requires an explicit identity"
                    )
                if not isinstance(child_lineage, LineageBinding):
                    raise LineageIdentityError(
                        "child identity provider returned an invalid binding"
                    )
                relationship = lineage_relationship(
                    "spawn", ctx.deps.lineage, child_lineage
                )
                _emit_lineage(
                    ctx.deps,
                    "child_spawned",
                    relationships=(relationship,),
                    child_run_id=child_lineage.run_id,
                    child_node=child_lineage.local_id,
                )
            child = _Env(
                text=ctx.deps.text[start : end or None],
                depth=ctx.deps.depth + 1,
                config=ctx.deps.config,
                budget=ctx.deps.budget,
                ports=ctx.deps.ports,
                lineage=child_lineage,
                child_identity_provider=ctx.deps.child_identity_provider,
            )
            _emit_lineage(child, "child_started")
            out = str(await _run_drained(question, child, str))
        if ctx.deps.ports.engine_decision_port() is not None:
            if ctx.deps.sub_call_signature is not None:
                validate_sub_output(ctx.deps.sub_call_signature, out)
            await choose_mode(ctx.deps, out, ctx.deps.ports)
        return out

    return agent


def _prompt(task: str, env: _Env) -> str:
    preview = env.text[: env.config.preview_chars]
    return f"{task}\n\n[input: {len(env.text)} chars; preview]\n{preview}"


async def _run_level(task: str, env: _Env, output_type: Any) -> Any:
    limits = UsageLimits(
        request_limit=None,
        tool_calls_limit=env.budget.limits.tool_calls_limit,
    )
    result = await _agent(
        env.config, env.depth, output_type, env.budget, env.ports
    ).run(_prompt(task, env), deps=env, usage_limits=limits)
    return result.output


async def _run_drained(task: str, env: _Env, output_type: Any) -> Any:
    """Cancel the framework once, then shield all descendant cleanup.

    The level's run status follows the run machine: a cancellation moves it
    through ``Cancelling`` and the finished drain to ``Stopped(cancelled)``;
    a failed level to ``Failed``; a clean finish to ``Completed``.
    """
    status = RunStatus.RUNNING
    child = asyncio.create_task(_run_level(task, env, output_type))
    interrupted: BaseException | None = None
    value: Any = None
    try:
        value = await asyncio.shield(child)
    except BaseException as exc:
        interrupted = exc
    finally:
        if not child.done():
            child.cancel()
        drain = asyncio.gather(child, return_exceptions=True)
        while not drain.done():
            try:
                await asyncio.shield(drain)
            except asyncio.CancelledError as exc:
                # Preserve the original cancellation while refusing to deliver
                # another cancellation into a descendant's async finally block.
                interrupted = interrupted or exc
        # The drain has finished: the level's run state follows the machine.
        if interrupted is not None and isinstance(
            interrupted, (asyncio.CancelledError, GeneratorExit)
        ):
            status = transition(status, RunEvent.CANCEL)
            status = transition(
                status, RunEvent.DRAIN_COMPLETE, {"no_active_task": True}
            )
        elif interrupted is not None:
            status = transition(status, RunEvent.FAIL)
        else:
            status = transition(status, RunEvent.FINISH, {"output_task_complete": True})
    if interrupted is not None:
        raise interrupted
    return value


async def _config_from_host(task: str, ports: HostPorts) -> RLMConfig:
    plan = await resolve_plan(RunRequest(task=task, agent_name="rlm", ports=ports), {})
    return RLMConfig(
        model=plan.model,
        request_limit=50
        if plan.budget.request_limit is None
        else plan.budget.request_limit,
        total_tokens_limit=plan.budget.total_tokens_limit,
        timeout_seconds=plan.budget.timeout_seconds,
        max_concurrent_requests=plan.budget.max_concurrent_requests,
        tool_calls_limit=50
        if plan.budget.tool_calls_limit is None
        else plan.budget.tool_calls_limit,
    )


async def run_rlm(
    task: str,
    input_text: str = "",
    *,
    config: RLMConfig | None = None,
    output_type: Any = str,
    lineage: LineageBinding | None = None,
    child_identity_provider: Callable[[LineageBinding, str], LineageBinding | None]
    | None = None,
    document: Mapping[str, Any] | None = None,
    ports: HostPorts | None = None,
) -> dict[str, Any]:
    """Run one recursive task over ``input_text``.

    ``document`` names a served document reference
    (``document_id``, optional ``revision`` and ``toc_digest``); with it,
    ``read_input`` reads through the engine's ``ReadSpan`` instead of
    slicing ``input_text``.

    Returns ``{"ok": True, "result", "task", "usage", "max_depth"}`` or
    ``{"ok": False, "error", "failure_class", "task"}``. A
    :class:`SandboxFatalError` propagates so the caller fails fast.
    """
    try:
        if lineage is not None and not isinstance(lineage, LineageBinding):
            raise TypeError("lineage must be a LineageBinding when supplied")
        ports = ports if ports is not None else HostPorts()
        if ports.run_checkpoint_bound():
            raise ValueError("recursive checkpoint restore is unavailable")
        history_request = (
            RunRequest(task=task, agent_name="rlm", run_id=lineage.run_id, ports=ports)
            if lineage is not None
            else None
        )
        if await read_recursive_run_history(ports, history_request) is not None:
            raise ValueError("recursive conversation history is unavailable")
        cfg = config if config is not None and config.model else None
        cfg = cfg or await _config_from_host(task, ports)
        for name in ("max_concurrent_requests",):
            value = getattr(cfg, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        for name in (
            "request_limit",
            "tool_calls_limit",
            "total_tokens_limit",
            "max_depth",
        ):
            value = getattr(cfg, name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"{name} must be a nonnegative integer")
        budget = SharedBudget(
            RunBudget(
                request_limit=cfg.request_limit,
                tool_calls_limit=cfg.tool_calls_limit,
                total_tokens_limit=cfg.total_tokens_limit,
            ),
            max_concurrent_requests=cfg.max_concurrent_requests,
        )
        if child_identity_provider is not None and lineage is None:
            raise ValueError("child identity provider requires a root lineage")
        document_id: str | None = None
        document_revision = "1"
        toc_digest: str | None = None
        if document is not None:
            document_id = document.get("document_id")
            if not isinstance(document_id, str) or not document_id:
                raise ValueError("a document reference needs a document_id")
            document_revision = document.get("revision", "1")
            if not isinstance(document_revision, str) or not document_revision:
                raise ValueError("a document reference needs a revision")
            toc_raw = document.get("toc_digest")
            if toc_raw is not None and (not isinstance(toc_raw, str) or not toc_raw):
                raise ValueError("a document reference's toc_digest must be a string")
            toc_digest = toc_raw
        env = _Env(
            text=input_text,
            depth=0,
            config=cfg,
            budget=budget,
            ports=ports,
            lineage=lineage,
            child_identity_provider=child_identity_provider,
            document_id=document_id,
            document_revision=document_revision,
            toc_digest=toc_digest,
        )
        _emit_lineage(env, "run_started")
        split = PlanSplitResult(False, None, 0, None)
        if ports.engine_decision_port() is not None:
            split = await plan_split(task, input_text, ports, run_ref=_run_ref(env))
        if split.split:
            value = split.answer
        else:
            async with asyncio.timeout(cfg.timeout_seconds):
                value = await _run_drained(task, env, output_type)
    except SandboxFatalError:
        raise
    except Exception as exc:  # noqa: BLE001 - the entry returns a typed failure
        return {
            "ok": False,
            "error": str(exc),
            "failure_class": classify_failure(exc),
            "task": task,
        }
    usage = {
        "requests": env.budget.usage.requests,
        "input_tokens": env.budget.usage.input_tokens,
        "output_tokens": env.budget.usage.output_tokens,
        "total": env.budget.usage.input_tokens + env.budget.usage.output_tokens,
    }
    return {
        "ok": True,
        "result": value,
        "task": task,
        "usage": usage,
        "max_depth": cfg.max_depth,
        "split_model_calls": split.model_calls,
    }


# ---------------------------------------------------------------------------
# Engine-decision consumers of the recursive run (MERISTEM-HARNESS-CYCLE-
# R017.1, R017.3, R017.5, R027.1).
#
# Each consumer reaches the decision engine only through the host-bound
# :class:`meristem.host_ports.EngineDecisionPort` and applies the answer it
# receives without deciding anything of its own. An unbound port refuses
# ``engine_decision_unavailable`` and decides nothing; an engine refusal
# passes through under the engine's closed code; a malformed received value
# refuses the consumer's closed local code.

#: The window size one context-budget decision is sent against.
DEFAULT_WINDOW_TOKENS = 8_192


def _token_estimate(text: str) -> int:
    """A coarse prompt size in tokens: one token per four characters."""
    return max(1, (len(text) + 3) // 4)


@dataclass(frozen=True, slots=True)
class PlanSplitResult:
    """One over-window split decision, as applied."""

    split: bool
    answer: str | None
    model_calls: int
    record_digest: str | None


async def plan_split(
    task: str, input_text: str, port: HostPorts, *, run_ref: str = "rlm"
) -> PlanSplitResult:
    """Split an over-window prompt into chunked, pairwise-merged model calls.

    The prompt size goes to one ``context_budget`` decision; the harness
    applies the received ``exceeds``, ``chunks``, ``depth``,
    ``chunk_tokens`` and ``model_calls`` without deciding anything of its
    own. When the answer says the prompt exceeds its window, the input is
    split into slices of ``chunk_tokens`` (up to ``chunks``) and the leaf
    answers merge pairwise until one answer remains. A plan that would
    issue more calls than ``model_calls`` refuses
    ``split_exceeds_model_calls`` and a merge plan deeper than the received
    depth refuses ``split_plan_invalid``; engine codes pass through; an
    unbound port refuses ``engine_decision_unavailable`` and decides
    nothing. The five received options and the answer's ``record_digest``
    enter the decision record.
    """
    engine = port.engine_decision_port()
    if engine is None:
        raise EngineDecisionRefused("engine_decision_unavailable")
    answer = await engine.decide(
        "context_budget",
        {
            "prompt_tokens": _token_estimate(input_text),
            "window_tokens": DEFAULT_WINDOW_TOKENS,
            "model_profile_ref": "rlm",
        },
        run_ref=run_ref,
    )
    if answer.refusal_code is not None:
        raise ConsumerRefused(answer.refusal_code)
    if answer.outcome not in ("solved", "committed"):
        # An abstained or advisory answer decides nothing: the prompt runs
        # whole.
        return PlanSplitResult(False, None, 0, answer.record_digest)
    payload = answer.payload
    for name, shape in (
        ("exceeds", bool),
        ("chunks", int),
        ("depth", int),
        ("chunk_tokens", int),
        ("model_calls", int),
    ):
        if type(payload.get(name)) is not shape:
            raise ConsumerRefused("split_plan_invalid")
    if payload["exceeds"] is not True:
        return PlanSplitResult(False, None, 0, answer.record_digest)
    chunks = payload["chunks"]
    chunk_tokens = payload["chunk_tokens"]
    model_calls = payload["model_calls"]
    if chunks < 1 or chunk_tokens < 1 or model_calls < 1:
        raise ConsumerRefused("split_plan_invalid")
    pieces = [
        input_text[index : index + chunk_tokens]
        for index in range(0, len(input_text), chunk_tokens)
    ][:chunks]
    if not pieces:
        pieces = [""]
    merge_levels, size = 0, len(pieces)
    while size > 1:
        merge_levels += 1
        size = (size + 1) // 2
    if merge_levels > payload["depth"]:
        raise ConsumerRefused("split_plan_invalid")
    if 2 * len(pieces) - 1 > model_calls:
        raise ConsumerRefused("split_exceeds_model_calls")
    model = port.resolve_model("rlm")
    leaves = await asyncio.gather(
        *(
            _model_call(model, f"{task}\n[chunk {number} of {len(pieces)}]\n{piece}")
            for number, piece in enumerate(pieces, 1)
        )
    )
    level = list(leaves)
    while len(level) > 1:
        merged = []
        for index in range(0, len(level), 2):
            if index + 1 < len(level):
                merged.append(
                    await _model_call(
                        model,
                        f"{task}\n[merge]\n{level[index]}\n---\n{level[index + 1]}",
                    )
                )
            else:
                merged.append(level[index])
        level = merged
    return PlanSplitResult(
        True,
        level[0],
        2 * len(pieces) - 1,
        answer.record_digest,
    )


@dataclass(frozen=True, slots=True)
class ContextBudgetSignals:
    """The measured context-budget signals of one tool result."""

    run_id: str
    tool_output_bytes: int
    tool_output_digest: str
    tool_call_ref: str

    def digest(self) -> str:
        """The canonical digest of the four measured fields."""
        return _canonical_digest(
            {
                "run_id": self.run_id,
                "tool_output_bytes": self.tool_output_bytes,
                "tool_output_digest": self.tool_output_digest,
                "tool_call_ref": self.tool_call_ref,
            }
        )

    def params(self) -> dict[str, object]:
        """The decision record: the signals plus their digest."""
        return {
            "run_id": self.run_id,
            "tool_output_bytes": self.tool_output_bytes,
            "tool_output_digest": self.tool_output_digest,
            "tool_call_ref": self.tool_call_ref,
            "signals_digest": self.digest(),
        }


async def choose_mode(state: _Env, tool_result: object, port: HostPorts) -> str:
    """Choose direct or recursive execution after a tool call.

    The tool output's byte count and digest go to one ``recursion_mode``
    decision together with the run and tool-call identity and the signal
    digest; the harness runs the returned mode, ``direct`` or
    ``recursive``, without deciding anything of its own. A signal with a
    missing field refuses ``signal_incomplete``; engine codes pass
    through, including a stale signal; an unbound port refuses
    ``engine_decision_unavailable`` and decides nothing, keeping the
    state's current mode. The signal digest and the mode enter the
    decision record.
    """
    engine = port.engine_decision_port()
    if engine is None:
        raise EngineDecisionRefused("engine_decision_unavailable")
    output = str(tool_result)
    signals = ContextBudgetSignals(
        run_id=_run_ref(state),
        tool_output_bytes=len(output.encode("utf-8")),
        tool_output_digest=hashlib.sha256(output.encode("utf-8")).hexdigest(),
        tool_call_ref=state.tool_call_ref,
    )
    if not signals.tool_call_ref:
        raise ConsumerRefused("signal_incomplete")
    answer = await engine.decide(
        "recursion_mode", signals.params(), run_ref=signals.run_id
    )
    if answer.refusal_code is not None:
        raise ConsumerRefused(answer.refusal_code)
    if answer.outcome not in ("solved", "committed"):
        # An abstained answer decides nothing: the current mode holds.
        return state.recursion_mode or "recursive"
    mode = answer.payload.get("mode")
    if mode not in ("direct", "recursive"):
        raise ConsumerRefused("recursion_mode_invalid")
    state.recursion_mode = mode
    return mode


@dataclass(frozen=True, slots=True)
class SubCallRequest:
    """The digests one sub-call admission travels with."""

    signature_digest: str
    model_digest: str
    tool_digest: str
    parent_ref: str


@dataclass(frozen=True, slots=True)
class SubCallAdmission:
    """The engine's admission of one sub-call, as received."""

    admitted: bool
    record_digest: str | None
    signature_digest: str
    model_digest: str
    tool_digest: str


async def admit_sub_call(
    call: SubCallRequest, port: HostPorts, *, run_ref: str = "rlm"
) -> SubCallAdmission:
    """Admit one sub-call through the engine before it starts.

    The signature, model and tool digests go to one
    ``sub_call_admission`` decision; the call runs only when the answer
    admits it. Engine codes ``SUBCALL_SIGNATURE_UNKNOWN``,
    ``SUBCALL_SIGNATURE_CHANGED``, ``SUBCALL_TOOL_NOT_ADMITTED``,
    ``SUBCALL_INPUT_SCHEMA_MISMATCH`` and ``SUBCALL_PARENT_MISSING`` pass
    through; a request with a missing digest refuses
    ``sub_call_request_incomplete``; an unbound port refuses
    ``engine_decision_unavailable`` and the guarded call does not start.
    The three digests enter the span record.
    """
    engine = port.engine_decision_port()
    if engine is None:
        raise EngineDecisionRefused("engine_decision_unavailable")
    for value in (
        call.signature_digest,
        call.model_digest,
        call.tool_digest,
        call.parent_ref,
    ):
        if not value:
            raise ConsumerRefused("sub_call_request_incomplete")
    answer = await engine.decide(
        "sub_call_admission",
        {
            "signature_digest": call.signature_digest,
            "model_digest": call.model_digest,
            "tool_digest": call.tool_digest,
            "parent_ref": call.parent_ref,
        },
        run_ref=run_ref,
    )
    if answer.refusal_code is not None:
        raise ConsumerRefused(answer.refusal_code)
    return SubCallAdmission(
        admitted=answer.outcome in ("solved", "committed"),
        record_digest=answer.record_digest,
        signature_digest=call.signature_digest,
        model_digest=call.model_digest,
        tool_digest=call.tool_digest,
    )


def validate_sub_output(signature: Mapping[str, Any], output: object) -> bool:
    """Validate a sub-call output against its signature's output schema.

    Success is recorded only on a match; a mismatching output refuses
    ``sub_call_output_mismatch`` and a signature without a usable output
    schema refuses ``sub_call_signature_invalid``.
    """
    output_types = {
        "str": str,
        "int": int,
        "float": float,
        "bool": bool,
        "list": list,
        "dict": dict,
    }
    schema = signature.get("output_schema")
    if isinstance(schema, Mapping):
        type_name = schema.get("type")
        expected = output_types.get(type_name) if isinstance(type_name, str) else None
        if expected is None:
            raise ConsumerRefused("sub_call_signature_invalid")
        if type(output) is not expected:
            raise ConsumerRefused("sub_call_output_mismatch")
        keys = schema.get("keys")
        if isinstance(keys, (list, tuple)) and type(output) is dict:
            if set(output) != {str(key) for key in keys}:
                raise ConsumerRefused("sub_call_output_mismatch")
        return True
    if isinstance(schema, str):
        expected = output_types.get(schema)
        if expected is None:
            raise ConsumerRefused("sub_call_signature_invalid")
        if type(output) is not expected:
            raise ConsumerRefused("sub_call_output_mismatch")
        return True
    raise ConsumerRefused("sub_call_signature_invalid")


@dataclass
class ThoughtNode:
    """One thought node with its pre-expanded candidate children."""

    id: str
    text: str
    depth: int = 0
    children: tuple[ThoughtNode, ...] = ()
    cost: int = 0


@dataclass
class ThoughtArena:
    """The harness-held thought search state and its received bounds.

    The bounds are the received search limits; an arena that exceeds
    them refuses ``arena_over_budget``.
    """

    nodes: dict[str, ThoughtNode]
    frontier: tuple[str, ...]
    expanded: tuple[str, ...] = ()
    best_path: tuple[str, ...] = ()
    tokens_spent: int = 0
    beam_width: int = 8
    max_branching: int = 4
    max_depth: int = 3
    max_nodes: int = 64
    max_tokens: int = 4_096


@dataclass(frozen=True, slots=True)
class ThoughtSearchResult:
    """The ended thought search: the best-so-far path and its stop reason."""

    path: tuple[str, ...]
    stop_reason: str
    expanded: tuple[str, ...]


def _frontier_summary(arena: ThoughtArena) -> list[dict[str, object]]:
    """The bounded frontier summary one thought-search decision carries."""
    return [
        {
            "id": node_id,
            "text": arena.nodes[node_id].text[:64],
            "depth": arena.nodes[node_id].depth,
        }
        for node_id in arena.frontier[: arena.beam_width]
    ]


def _arena_budget(arena: ThoughtArena) -> dict[str, int]:
    """The spent budget and sent bounds one thought-search decision carries."""
    return {
        "tokens_spent": arena.tokens_spent,
        "beam_width": arena.beam_width,
        "max_branching": arena.max_branching,
        "max_depth": arena.max_depth,
        "max_nodes": arena.max_nodes,
        "max_tokens": arena.max_tokens,
    }


def _check_arena_bounds(arena: ThoughtArena) -> None:
    """Refuse arena_over_budget when the arena exceeds the sent bounds."""
    if (
        len(arena.nodes) > arena.max_nodes
        or len(arena.frontier) > arena.beam_width
        or arena.tokens_spent > arena.max_tokens
        or any(node.depth > arena.max_depth for node in arena.nodes.values())
    ):
        raise ConsumerRefused("arena_over_budget")


async def expand_thoughts(
    arena: ThoughtArena, port: HostPorts, *, run_ref: str = "rlm"
) -> ThoughtSearchResult:
    """Expand the thought nodes the engine chooses, until it stops the search.

    After each expansion the bounded frontier summary and the spent
    budget go to one ``thought_search`` decision; the harness expands
    only the nodes the answer chooses and stops on the answer's stop
    with its best-so-far path. An arena that exceeds the sent bounds
    refuses ``arena_over_budget``; engine codes pass through; an unbound
    port refuses ``engine_decision_unavailable`` and decides nothing.
    The chosen node ids and the stop reason enter the decision record.
    """
    engine = port.engine_decision_port()
    if engine is None:
        raise EngineDecisionRefused("engine_decision_unavailable")
    _check_arena_bounds(arena)
    while arena.frontier:
        answer = await engine.decide(
            "thought_search",
            {
                "frontier": _frontier_summary(arena),
                "budget": _arena_budget(arena),
            },
            run_ref=run_ref,
        )
        if answer.refusal_code is not None:
            raise ConsumerRefused(answer.refusal_code)
        if answer.outcome not in ("solved", "committed"):
            # An abstained answer decides nothing: no node is chosen and
            # the search ends with the best-so-far path.
            return ThoughtSearchResult(arena.best_path, "abstained", arena.expanded)
        payload = answer.payload
        best_raw = payload.get("best_path")
        if isinstance(best_raw, (list, tuple)):
            best = tuple(str(item) for item in best_raw)
            if all(node_id in arena.nodes for node_id in best):
                arena.best_path = best
        chosen_raw = payload.get("nodes", ())
        if not isinstance(chosen_raw, (list, tuple)):
            raise ConsumerRefused("thought_selection_invalid")
        chosen = tuple(str(item) for item in chosen_raw)
        if (
            len(set(chosen)) != len(chosen)
            or len(chosen) > arena.beam_width
            or any(node_id not in arena.frontier for node_id in chosen)
        ):
            raise ConsumerRefused("thought_selection_invalid")
        if payload.get("stop") is True and not chosen:
            return ThoughtSearchResult(
                arena.best_path,
                str(payload.get("stop_reason") or "stopped"),
                arena.expanded,
            )
        for node_id in chosen:
            node = arena.nodes[node_id]
            if len(node.children) > arena.max_branching:
                raise ConsumerRefused("arena_over_budget")
            arena.tokens_spent += node.cost
            arena.expanded = arena.expanded + (node_id,)
            added = [child.id for child in node.children if child.id not in arena.nodes]
            for child in node.children:
                if child.id not in arena.nodes:
                    arena.nodes[child.id] = replace(child, depth=node.depth + 1)
            arena.frontier = tuple(
                item for item in arena.frontier if item != node_id
            ) + tuple(added)
        _check_arena_bounds(arena)
        if payload.get("stop") is True:
            return ThoughtSearchResult(
                arena.best_path,
                str(payload.get("stop_reason") or "stopped"),
                arena.expanded,
            )
    return ThoughtSearchResult(arena.best_path, "frontier_exhausted", arena.expanded)


__all__ = [
    "ContextBudgetSignals",
    "DEFAULT_WINDOW_TOKENS",
    "PlanSplitResult",
    "ROOT_INSTRUCTIONS",
    "RLMConfig",
    "SubCallAdmission",
    "SubCallRequest",
    "ThoughtArena",
    "ThoughtNode",
    "ThoughtSearchResult",
    "admit_sub_call",
    "choose_mode",
    "expand_thoughts",
    "plan_split",
    "run_rlm",
    "validate_sub_output",
]
