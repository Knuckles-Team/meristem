"""The one composition apply and the selected system prompt of the run loop.

Plan resolution applies the assembled primitive composition the engine
returns and the system prompt whose render receipt verifies; the
harness adds nothing and drops nothing.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineAnswer, EngineDecisionPort

#: The closed primitive kinds the composition may carry.
_PRIMITIVE_KINDS = frozenset({"prompt", "tool", "memory", "control"})


async def assemble_plan(
    request: Any, plan: Any, port: EngineDecisionPort | None, *, run_ref: str
) -> Any:
    """Apply the assembled primitive composition.

    One served assembly answer carries the ordered primitives (prompt,
    tool, memory, control flow). The plan resolves to
    ``dataclasses.replace(plan, steps=..., tool_names=...)`` with
    exactly those primitives in that order: the harness adds nothing
    and drops nothing. A primitive kind outside the four refuses
    ``composition_unsupported``; engine certificate refusals pass
    through. The certificate digest and the record digest enter the
    trace. With no bound port the plan resolves unchanged and the step
    decides nothing.
    """
    if port is None:
        return plan
    if isinstance(request, Mapping):
        request_params = dict(request)
    else:
        request_params = {
            "task": request.task,
            "run_id": request.run_id,
            "mode": request.mode,
            "budget_tokens": request.budget_tokens,
        }
    answer = await port.assemble(request_params, run_ref=run_ref)
    if answer.refusal_code is not None:
        raise ConsumerRefused(answer.refusal_code)
    primitives = (
        answer.payload.get("primitives")
        if answer.outcome in ("solved", "committed")
        else None
    )
    if primitives is None:
        return plan
    if not isinstance(primitives, (list, tuple)) or not all(
        isinstance(primitive, Mapping) for primitive in primitives
    ):
        raise ConsumerRefused("composition_unsupported")
    steps: list[str] = []
    tool_names: list[str] = []
    for primitive in primitives:
        kind = primitive.get("kind")
        name = primitive.get("name")
        if kind not in _PRIMITIVE_KINDS or type(name) is not str or not name:
            raise ConsumerRefused("composition_unsupported")
        steps.append(name)
        if kind == "tool":
            tool_names.append(name)
    return replace(plan, steps=tuple(steps), tool_names=tuple(tool_names))


def apply_system_prompt(plan: Any, answer: EngineAnswer) -> Any:
    """Apply the selected system prompt with a verified digest.

    The render receipt is verified first (MERISTEM-HARNESS-CYCLE-R076)
    before the prompt is applied. The ``system_prompt`` component of
    the assembly answer carries ``component_ref``, ``digest`` and
    ``text``; the SHA-256 of ``text`` must equal ``digest``, else the
    step refuses ``prompt_digest_mismatch``. The plan's instructions
    become that text and each agent starts with that prompt only.
    Engine codes pass through; without a ``system_prompt`` component
    the plan keeps its own instructions.
    """
    if answer.refusal_code is not None:
        raise ConsumerRefused(answer.refusal_code)
    component = (
        answer.payload.get("system_prompt")
        if answer.outcome in ("solved", "committed")
        else None
    )
    if not isinstance(component, Mapping):
        return plan
    text = component.get("text")
    digest = component.get("digest")
    if type(text) is not str or not text:
        raise ConsumerRefused("prompt_digest_mismatch")
    computed = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if type(digest) is not str or computed != digest:
        raise ConsumerRefused("prompt_digest_mismatch")
    return replace(plan, instructions=text)


__all__ = ["assemble_plan", "apply_system_prompt"]
