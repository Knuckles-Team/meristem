"""Run a skill in a worker child run and return a bounded summary (R095)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from meristem.harness.skill_loader import count_tokens

WORKER_MODEL_NOT_ALLOWED = "WORKER_MODEL_NOT_ALLOWED"


class WorkerModelNotAllowed(RuntimeError):
    """The worker model is outside the plan's allowed models."""

    code = WORKER_MODEL_NOT_ALLOWED


@dataclass(frozen=True, slots=True)
class WorkerPlan:
    """Received worker settings for one skill."""

    worker_model: str
    allowed_models: Sequence[str]
    mode: Literal["foreground", "background"] = "foreground"
    summary_max_tokens: int = 500


@dataclass(frozen=True, slots=True)
class SkillSummary:
    skill_ref: str
    text: str
    truncated: bool


#: ``(model, skill_ref, lineage) -> full child output``; the host starts the
#: child run with the lineage fields of the parent.
ChildRun = Callable[[str, str, dict[str, str]], Awaitable[str]]
#: Delivers one summary to the parent at its next step boundary.
Deliver = Callable[[SkillSummary], Awaitable[None]]


def bound_summary(skill_ref: str, text: str, max_tokens: int) -> SkillSummary:
    """Cut ``text`` at ``max_tokens`` and mark the cut."""
    if count_tokens(text) <= max_tokens:
        return SkillSummary(skill_ref, text, False)
    return SkillSummary(skill_ref, text[: max_tokens * 4], True)


async def run_skill_in_worker(
    skill_ref: str,
    plan: WorkerPlan,
    *,
    parent_run_id: str,
    run_child: ChildRun,
    deliver: Deliver,
) -> SkillSummary | asyncio.Task[None]:
    """Run the skill on the received model; the parent gets the summary only.

    Foreground returns the summary. Background returns a task that delivers
    the summary through ``deliver`` and lets the parent keep running.
    """
    if plan.worker_model not in plan.allowed_models:
        raise WorkerModelNotAllowed(WORKER_MODEL_NOT_ALLOWED)
    lineage = {"parent_run_id": parent_run_id, "skill_ref": skill_ref}

    async def work() -> SkillSummary:
        text = await run_child(plan.worker_model, skill_ref, lineage)
        return bound_summary(skill_ref, text, plan.summary_max_tokens)

    if plan.mode == "background":

        async def background() -> None:
            await deliver(await work())

        return asyncio.create_task(background())
    return await work()


__all__ = [
    "WORKER_MODEL_NOT_ALLOWED",
    "SkillSummary",
    "WorkerModelNotAllowed",
    "WorkerPlan",
    "bound_summary",
    "run_skill_in_worker",
]
