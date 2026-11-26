"""Reuse a received program before the planning call (MERISTEM-HARNESS-CYCLE-R022).

The engine matches a stored program; the harness runs its bound steps through the
host tool caller and, when every step is ``ok``, answers from their results with
one model call. It holds no threshold, matcher or binder, and it never retries a
program. Any failure falls back to full planning.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import (
    EngineAnswer,
    EngineDecisionPort,
    EngineDecisionRefused,
    ToolStepResult,
)

TOOL_RESULT_LIMIT = 256 * 1024
MATCH_OPTION = "program_match:"


@dataclass(frozen=True, slots=True)
class BoundToolStep:
    index: int
    server: str
    tool: str
    arguments: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ReuseOutcome:
    outcome: Literal["hit", "fallback", "abstain", "skipped"]
    task_signature: str
    program_digest: str | None = None
    match_kind: str | None = None
    score_micros: int | None = None
    threshold_micros: int | None = None
    failed_step: int | None = None
    refusal_code: str | None = None
    record_digest: str | None = None
    results: tuple[Any, ...] = field(default=())

    def as_event(self) -> dict[str, Any]:
        """The ``program_reuse`` run event (R022.7)."""
        return {
            "type": "program_reuse",
            **{k: getattr(self, k) for k in _EVENT_FIELDS},
        }


_EVENT_FIELDS = (
    "outcome",
    "task_signature",
    "program_digest",
    "match_kind",
    "score_micros",
    "threshold_micros",
    "failed_step",
    "refusal_code",
    "record_digest",
)


def _scalar(value: Any) -> bool:
    return value is None or isinstance(value, str | int | float | bool)


def parse_program_match(
    answer: EngineAnswer,
) -> tuple[tuple[BoundToolStep, ...], dict[str, Any]]:
    """Read the ``program_match:`` option into bound steps and the match fields."""
    digest = next(
        (
            o[len(MATCH_OPTION) :]
            for o in answer.option_ids
            if o.startswith(MATCH_OPTION)
        ),
        None,
    )
    raw = answer.payload.get("steps")
    if not digest or not isinstance(raw, list):
        raise ConsumerRefused("program_match_invalid")
    steps: list[BoundToolStep] = []
    for index, item in enumerate(raw):
        if (
            not isinstance(item, Mapping)
            or not item.get("server")
            or not item.get("tool")
            or not isinstance(item.get("arguments", {}), Mapping)
            or not all(_scalar(v) for v in item.get("arguments", {}).values())
        ):
            raise ConsumerRefused("program_match_invalid")
        steps.append(
            BoundToolStep(
                index, str(item["server"]), str(item["tool"]), dict(item["arguments"])
            )
        )
    fields = {
        "program_digest": digest,
        "match_kind": answer.payload.get("match_kind"),
        "score_micros": answer.payload.get("score_micros"),
        "threshold_micros": answer.payload.get("threshold_micros"),
    }
    return tuple(steps), fields


def _bounded(value: Any) -> Any:
    text = json.dumps(value, sort_keys=True, default=str)
    return value if len(text) <= TOOL_RESULT_LIMIT else text[:TOOL_RESULT_LIMIT]


async def _replay(
    caller: Any, steps: tuple[BoundToolStep, ...]
) -> tuple[list[Any], int | None, str | None]:
    """Run each step once, in order; stop at the first one that is not ``ok``."""
    results: list[Any] = []
    for step in steps:
        if caller is None:
            return results, step.index, "tool_caller_unavailable"
        try:
            raw = await caller(
                step.server, step.tool, dict(step.arguments), f"program:{step.index}"
            )
        except Exception:  # noqa: BLE001 - a raised tool error is a failed step
            return results, step.index, "program_step_failed"
        outcome = raw if isinstance(raw, ToolStepResult) else ToolStepResult(result=raw)
        if outcome.outcome == "refused":
            return results, step.index, "program_step_refused"
        if outcome.outcome == "failed":
            return results, step.index, "program_step_failed"
        results.append(_bounded(outcome.result))
    return results, None, None


async def reuse_program(
    request: Any,
    plan: Any,
    state: Any,
    port: EngineDecisionPort | None,
    *,
    restricted: bool = False,
    restored: bool = False,
) -> tuple[Any, ReuseOutcome]:
    """Look a program up and, on a hit, replace the plan with one answer step."""
    config = plan.harness.program_reuse
    if config is None or not config.task_signature or not config.program_graph:
        raise ConsumerRefused("program_reuse_config_invalid")
    signature = config.task_signature
    if restricted or restored or plan.dependency_plan is not None:
        return plan, ReuseOutcome("skipped", signature)
    if port is None:
        raise EngineDecisionRefused("engine_decision_unavailable")
    answer = await port.decide(
        "program_lookup",
        {
            "task_signature": signature,
            "request_text": request.task,
            "program_graph": config.program_graph,
        },
        run_ref=state.run_id,
    )
    if answer.outcome == "abstained":
        return plan, ReuseOutcome(
            "abstain",
            signature,
            refusal_code=answer.refusal_code,
            record_digest=answer.record_digest,
        )
    if answer.refusal_code is not None:
        raise ConsumerRefused(answer.refusal_code)
    try:
        steps, match = parse_program_match(answer)
    except ConsumerRefused as invalid:
        return plan, ReuseOutcome(
            "fallback",
            signature,
            refusal_code=invalid.code,
            record_digest=answer.record_digest,
        )
    results, failed, code = await _replay(request.ports.tool_caller(), steps)
    base = {"task_signature": signature, "record_digest": answer.record_digest, **match}
    if failed is not None:
        context = json.dumps(results, sort_keys=True, default=str)
        first = f"{plan.steps[0]}\n\n{context[:TOOL_RESULT_LIMIT]}" if results else None
        kept = replace(plan, steps=(first, *plan.steps[1:])) if first else plan
        return kept, ReuseOutcome(
            "fallback",
            failed_step=failed,
            refusal_code=code,
            results=tuple(results),
            **base,
        )
    prompt = json.dumps({"task": request.task, "results": results}, sort_keys=True)
    answered = replace(plan, steps=(prompt,), tool_names=(), toolsets=())
    return answered, ReuseOutcome("hit", results=tuple(results), **base)


async def report_reuse(
    outcome: ReuseOutcome,
    state: Any,
    port: EngineDecisionPort | None,
    emit: Any,
) -> str | None:
    """Emit the ``program_reuse`` event; report any non-skipped outcome once (R022.7).

    A refused report becomes a ``program_reuse_report_refused`` event and never
    fails the run. Returns the refusal code, if any.
    """
    from meristem.harness import engine_step

    emit(outcome.as_event())
    if outcome.outcome == "skipped":
        return None
    body = {
        "kind": "program_replay",
        "run_id": state.run_id,
        "task_signature": outcome.task_signature,
        "decision_record_digest": outcome.record_digest,
        "outcome": outcome.outcome,
        "program_digest": outcome.program_digest,
        "failed_step": outcome.failed_step,
        "refusal_code": outcome.refusal_code,
    }
    try:
        await engine_step.record(port, "RunRecord", body, run_ref=state.run_id)
    except ConsumerRefused as refused:
        emit({"type": "program_reuse_report_refused", "refusal_code": refused.code})
        return refused.code
    return None


__all__ = [
    "BoundToolStep",
    "ReuseOutcome",
    "parse_program_match",
    "report_reuse",
    "reuse_program",
]
