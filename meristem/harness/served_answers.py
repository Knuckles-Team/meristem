"""Served-answer consumers: rank, delegation gate, grade loop, agent lifecycle, leased step, skill scaffold.

Each applies one engine answer and decides nothing (MERISTEM-HARNESS-CYCLE-R033, R041, R046, R050, R052, R053).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from meristem.harness import engine_step
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineDecisionPort

DISTILLED_PREMISE = "distilled:model_calls=0"


async def accept_rank(
    port: EngineDecisionPort | None, params: Mapping[str, Any], *, run_id: str
) -> dict[str, Any]:
    """Accept only a no-model-call rank answer; keep the engine order (R033.1)."""
    answer = await engine_step.decide(port, "Rank", params, run_ref=run_id)
    if DISTILLED_PREMISE not in answer.payload.get("premises", ()):
        raise ConsumerRefused("distilled_contract_violated")
    return {
        "order": list(answer.option_ids),
        "premises": list(answer.payload["premises"]),
    }


async def admit_delegation(
    port: EngineDecisionPort | None,
    *,
    run_id: str,
    params: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Start a child only when the quality gate admits (R050.1); a refusal raises."""
    answer = await engine_step.write(
        port,
        "DecisionEval",
        {"arm": "DelegationQuality", **(params or {})},
        run_ref=run_id,
    )
    if answer.payload.get("verdict") != "admit":
        raise ConsumerRefused("delegation_not_admitted")
    return {"verdict": "admit"}


async def grade_loop(
    port: EngineDecisionPort | None,
    *,
    run_id: str,
    outputs: Sequence[str],
    grade: Callable[[str], float | None],
    run_step: Callable[[], Awaitable[str]],
) -> list[str]:
    """Grade each output and loop on the engine's continue or stop (R053.1)."""
    results = list(outputs)
    for index in range(10_000):
        score = grade(results[-1])
        if score is None:
            raise ConsumerRefused("output_ungraded")
        answer = await engine_step.decide(
            port,
            "FeedbackLoop",
            {"grade": score, "key": f"{run_id}:{index}"},
            run_ref=run_id,
        )
        if answer.payload.get("action") != "continue":
            return results
        results.append(await run_step())
    raise ConsumerRefused("FEEDBACK_BOUND_INVALID")


async def advance_lifecycle(
    port: EngineDecisionPort | None,
    transitions: Sequence[Mapping[str, Any]],
    *,
    run_id: str,
    manifest_revision: str,
    expected_revision: str,
) -> list[str]:
    """Send each lifecycle transition as the engine type; hold no state machine (R052.1)."""
    if manifest_revision != expected_revision:
        raise ConsumerRefused("contract_revision_stale")
    sent: list[str] = []
    for transition in transitions:
        await engine_step.write(
            port, "AgentComponent", dict(transition), run_ref=run_id
        )
        sent.append(str(transition.get("type")))
    return sent


async def run_leased_step(
    port: EngineDecisionPort | None,
    *,
    run_id: str,
    loop_id: str,
    lease_epoch: int,
    requests: Sequence[Callable[[], Awaitable[Any]]],
) -> list[Any]:
    """Run the leased step; stop before a request once the epoch is stale (R046.1)."""
    done: list[Any] = []
    for request in requests:
        current = await engine_step.read(
            port, "Loops", {"loop_id": loop_id}, run_ref=run_id
        )
        if current.payload.get("epoch") != lease_epoch:
            raise ConsumerRefused("loop_lease_stale")
        done.append(await request())
    await engine_step.write(
        port, "Loops", {"loop_id": loop_id, "completion": True}, run_ref=run_id
    )
    return done


def scaffold_skills(workspace: Path, rows: Sequence[Mapping[str, Any]]) -> list[Path]:
    """Write ``skills/<candidate_id>/SKILL.md`` per row and nothing else (R041.1)."""
    ids = [str(row["candidate_id"]) for row in rows]
    if len(set(ids)) != len(ids):
        raise ConsumerRefused("skill_candidate_duplicate")
    written: list[Path] = []
    for row in rows:
        target = workspace / "skills" / str(row["candidate_id"]) / "SKILL.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "---\n"
            f"name: {row['name']}\n"
            f"description: {row['description']}\n"
            f"source_digest: {row['source_digest']}\n"
            "---\n",
            encoding="utf-8",
        )
        written.append(target)
    return written


__all__ = [
    "accept_rank",
    "admit_delegation",
    "advance_lifecycle",
    "grade_loop",
    "run_leased_step",
    "scaffold_skills",
]
