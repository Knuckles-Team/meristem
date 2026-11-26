"""Memory only through the five served routes (MERISTEM-HARNESS-CYCLE-R035, R038, R040).

The harness recalls, remembers and applies an approved consolidation plan. It
reaches no other memory method, injects only what the engine marks applied, and
decides nothing about retention.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from meristem.harness import engine_step
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineDecisionPort

READ_ROUTES = frozenset({"MemoryRecall"})
WRITE_ROUTES = frozenset(
    {"MemoryRemember", "Consolidate", "Reinforce", "DecayMemories"}
)


async def recall_applied_notes(
    port: EngineDecisionPort | None, *, run_id: str
) -> list[Mapping[str, Any]]:
    """Recall ``SessionNote`` memory and keep only notes the engine applied (R035.1)."""
    answer = await engine_step.read(
        port, "MemoryRecall", {"kind": "SessionNote"}, run_ref=run_id
    )
    return [n for n in answer.payload.get("notes", ()) if n.get("applied") is True]


async def capture_session_note(
    port: EngineDecisionPort | None,
    state: Any,
    note: Mapping[str, Any],
) -> Mapping[str, str] | None:
    """Capture one note after a completed run, once per run id (R035.1)."""
    if state.status.value != "completed":
        raise ConsumerRefused("note_run_not_successful")
    if state.consumer_results.get("note_captured"):
        return None
    await engine_step.record(
        port,
        "RecordSessionNote",
        {**note, "run_id": state.run_id},
        run_ref=state.run_id,
    )
    state.consumer_results["note_captured"] = True
    return {
        "note:id": str(note.get("note_id")),
        "note:digest": engine_step.digest_of(dict(note)),
        "note:run": state.run_id,
    }


async def recall_episodes(
    port: EngineDecisionPort | None, *, run_id: str, as_of_ms: int
) -> list[Mapping[str, Any]]:
    """Recall the session's episodes once at run start (R038.1)."""
    answer = await engine_step.read(
        port,
        "MemoryRecall",
        {"scope": "Episodes", "as_of_ms": as_of_ms},
        run_ref=run_id,
    )
    return list(answer.payload.get("episodes", ()))


async def seal_episode(
    port: EngineDecisionPort | None,
    *,
    run_id: str,
    session_id: str | None,
    transcript_revision: str,
    recalled: Sequence[Mapping[str, Any]] = (),
) -> str:
    """Seal the run as one episode at run end (R038.1); returns the episode id."""
    if not session_id:
        raise ConsumerRefused("session_id_missing")
    if any(
        e.get("session_id") == session_id
        and e.get("transcript_revision") not in (None, transcript_revision)
        for e in recalled
    ):
        raise ConsumerRefused("episode_revision_stale")
    episode_id = engine_step.digest_of({"session_id": session_id, "run_id": run_id})
    await engine_step.write(
        port,
        "MemoryRemember",
        {"episode_spec": {"episode_id": episode_id, "session_id": session_id}},
        run_ref=run_id,
    )
    return episode_id


async def apply_plan(
    port: EngineDecisionPort | None,
    plan: Sequence[Mapping[str, Any]],
    *,
    run_id: str,
    approval_ref: str | None,
) -> list[dict[str, Any]]:
    """Apply a consolidation plan in plan order, only with an approval (R040.1).

    Items name one of ``Consolidate``, ``Reinforce`` or ``DecayMemories``; any
    other route refuses ``memory_route_forbidden`` before a call is made.
    """
    if not approval_ref:
        raise ConsumerRefused("consolidation_unapproved")
    allowed = WRITE_ROUTES - {"MemoryRemember"}
    if any(item.get("route") not in allowed for item in plan):
        raise ConsumerRefused("memory_route_forbidden")
    applied: list[dict[str, Any]] = []
    for item in plan:
        await engine_step.write(
            port, str(item["route"]), dict(item.get("params", {})), run_ref=run_id
        )
        applied.append({"route": item["route"], "approval_ref": approval_ref})
    return applied


async def memory_call(
    port: EngineDecisionPort | None,
    method: str,
    params: Mapping[str, Any],
    *,
    run_id: str,
) -> Any:
    """``recall`` or ``remember`` by method name; any other refuses (R040.1)."""
    if method in READ_ROUTES:
        return await engine_step.read(port, method, params, run_ref=run_id)
    if method == "MemoryRemember":
        return await engine_step.write(port, method, params, run_ref=run_id)
    raise ConsumerRefused("memory_route_forbidden")


__all__ = [
    "apply_plan",
    "capture_session_note",
    "memory_call",
    "recall_applied_notes",
    "recall_episodes",
    "seal_episode",
]
