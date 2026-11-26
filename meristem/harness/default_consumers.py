"""The run loop's default consumer steps, bound once per run.

Each step runs only when its name is in ``HarnessConfig.consumers`` and reads
its received inputs from the request metadata. It applies the engine's answer
and decides nothing. The module is the one place these steps are bound to
lifecycle points.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from meristem.harness.context_prose import compile_context, read_facts, render_facts
from meristem.harness.critique import read_critique
from meristem.harness.replay import first_divergence
from meristem.harness.replay_intake import admit_replay_events
from meristem.harness.retrieval_step import read_discover_verdict, run_retrieval_round
from meristem.harness.run_lifecycle import ConsumerRefused, RunLifecycle
from meristem.harness.search_state import (
    SearchStateFields,
    restore_fields,
    save_search_state,
)

CONTEXT_FACTS = "context_facts"
TRACE_CRITIQUE = "trace_critique"
SEARCH_STATE = "search_state"
REPLAY_DIGESTS = "replay_digests"
REPLAY_INTAKE = "replay_intake"
RETRIEVAL_ROUND = "retrieval_round"
DISCOVER_VERDICT = "discover_verdict"


def _received(request: Any, key: str) -> dict[str, Any] | None:
    value = request.metadata.get(key)
    return dict(value) if isinstance(value, dict) else None


def bind_default_consumers(
    lifecycle: RunLifecycle, request: Any, state: Any
) -> Callable[[], Awaitable[None]] | None:
    """Bind the admitted default steps; return the checkpoint restore step.

    The returned step (or ``None``) restores the search state before a
    restored checkpoint reaches the history (MERISTEM-HARNESS-CYCLE-R034.2).
    """
    ports = request.ports
    run_ref = request.run_id

    if lifecycle.enabled(CONTEXT_FACTS) and (
        params := _received(request, CONTEXT_FACTS)
    ):

        async def facts_step(current: Any) -> None:
            if current.compiled_context is not None:
                return
            result = await read_facts(
                ports.engine_decision_port(),
                node_ids=params["node_ids"],
                token_budget=params["token_budget"],
                max_facts_per_node=params.get("max_facts_per_node"),
                run_ref=run_ref,
            )
            compiled = compile_context(
                render_facts(result),
                demonstrations=tuple(params.get("demonstrations", ())),
                omitted_count=result.omitted_count,
            )
            current.compiled_context = compiled.text
            ports.emit_run_event(
                {
                    "type": "context_compiled",
                    "byte_count": compiled.byte_count,
                    "fact_ids": list(compiled.fact_ids),
                    "omitted_count": compiled.omitted_count,
                }
            )

        lifecycle.add(CONTEXT_FACTS, "before_step", facts_step)

    if lifecycle.enabled(TRACE_CRITIQUE) and (
        query := _received(request, TRACE_CRITIQUE)
    ):

        async def critique_step(current: Any, step: Any) -> None:
            pages = await read_critique(
                query, ports.engine_decision_port(), run_ref=run_ref
            )
            current.consumer_results[TRACE_CRITIQUE] = pages
            ports.emit_run_event(
                {
                    "type": "critique_read",
                    "cursors": [page["cursor"] for page in pages],
                }
            )

        lifecycle.add(TRACE_CRITIQUE, "after_step", critique_step)

    search = _received(request, SEARCH_STATE)
    restore_step = None
    if lifecycle.enabled(SEARCH_STATE) and search:

        async def save_step(current: Any, step: Any) -> None:
            revision = current.consumer_results.get("search_revision")
            if revision is None:
                revision = int(search.get("revision", 0))
            fields = SearchStateFields(
                transcript=tuple(current.outputs),
                evidence=tuple(search.get("evidence", ())),
                constraints=tuple(search.get("constraints", ())),
                open_claims=tuple(search.get("open_claims", ())),
            )
            saved = await save_search_state(
                fields,
                ports.engine_decision_port(),
                search_run_id=search["search_run_id"],
                doc_id=search["doc_id"],
                previous_revision=revision,
                request_id=f"{run_ref}:{revision + 1}",
                run_ref=run_ref,
            )
            current.consumer_results["search_revision"] = saved.revision
            ports.emit_run_event(
                {
                    "type": "search_state_saved",
                    "search_run_id": search["search_run_id"],
                    "state_revision": saved.revision,
                    "digests": dict(saved.digests),
                }
            )

        lifecycle.add(SEARCH_STATE, "after_step", save_step)

        async def restore_step() -> None:
            fields = await restore_fields(
                ports.engine_decision_port(),
                doc_id=search["doc_id"],
                revision=search.get("revision"),
                run_ref=run_ref,
            )
            state.consumer_results["search_state"] = fields

    if lifecycle.enabled(REPLAY_DIGESTS) and (
        stored := request.metadata.get("stored_step_digests")
    ):

        async def replay_step(_request: Any, _plan: Any) -> None:
            index = first_divergence(request.replay_events, list(stored))
            state.consumer_results[REPLAY_DIGESTS] = index
            ports.emit_run_event({"type": "replay_compared", "first_divergence": index})
            if index is not None:
                raise ConsumerRefused("replay_diverged")

        lifecycle.add(REPLAY_DIGESTS, "on_start", replay_step)

    if lifecycle.enabled(REPLAY_INTAKE):

        async def intake_step(_request: Any, _plan: Any) -> None:
            state.consumer_results[REPLAY_INTAKE] = admit_replay_events(
                request.replay_events
            )

        lifecycle.add(REPLAY_INTAKE, "on_start", intake_step)

    if lifecycle.enabled(RETRIEVAL_ROUND) and (
        round_args := _received(request, RETRIEVAL_ROUND)
    ):

        async def round_step(current: Any) -> None:
            if RETRIEVAL_ROUND in current.consumer_results:
                return
            outcome = await run_retrieval_round(
                ports,
                plan=round_args["plan"],
                counters=round_args.get("counters", {}),
                prior=round_args.get("prior"),
                run_id=run_ref,
            )
            current.consumer_results[RETRIEVAL_ROUND] = outcome
            ports.emit_run_event(
                {
                    "type": "retrieval_round_applied",
                    "stop": outcome.stop,
                    "abstained": outcome.abstained,
                }
            )

        lifecycle.add(RETRIEVAL_ROUND, "before_step", round_step)

    if lifecycle.enabled(DISCOVER_VERDICT) and (
        discover := _received(request, DISCOVER_VERDICT)
    ):

        async def discover_step(current: Any) -> None:
            if DISCOVER_VERDICT in current.consumer_results:
                return
            report = await read_discover_verdict(
                ports, request=discover, run_id=run_ref
            )
            current.consumer_results[DISCOVER_VERDICT] = report
            ports.emit_run_event(
                {
                    "type": "discover_verdict_read",
                    "gate_passed": report.gate_passed,
                    "failure_modes": list(report.failure_modes),
                }
            )

        lifecycle.add(DISCOVER_VERDICT, "before_step", discover_step)

    return restore_step
