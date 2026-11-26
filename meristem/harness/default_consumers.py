"""The run loop's default consumer steps, bound once per run.

Each step runs only when its name is in ``HarnessConfig.consumers`` and reads
its received inputs from the request metadata. It applies the engine's answer
and decides nothing. The module is the one place these steps are bound to
lifecycle points.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from time import perf_counter_ns
from typing import Any

from meristem.harness import engine_step
from meristem.harness.certification import submit_certification_receipt
from meristem.harness.context_prose import compile_context, read_facts, render_facts
from meristem.harness.critique import read_critique
from meristem.harness.held_item import apply_held_item
from meristem.harness.ingest_readback import verify_ingest_readback
from meristem.harness.nary_proposals import read_nary_proposals
from meristem.harness.org_runtime import advance_instance, staff_run
from meristem.harness.replay import first_divergence
from meristem.harness.replay_intake import admit_replay_events
from meristem.harness.retrieval_step import (
    read_discover_verdict,
    run_context_compile,
    run_retrieval_round,
)
from meristem.harness.risk_order import request_historical_risk
from meristem.harness.run_lifecycle import ConsumerRefused, RunLifecycle
from meristem.harness.search_state import (
    SearchStateFields,
    restore_fields,
    save_search_state,
)
from meristem.harness.span_export import build_spans, export_spans
from meristem.harness.summary_receipt import invoke_summary
from meristem.harness.technique_benchmark import TECHNIQUES, emit_technique_receipts
from meristem.harness.turn_timing import record_turn_timing
from meristem.harness.walkthrough import (
    build_walkthrough_manifest,
    send_walkthrough_manifest,
)
from meristem.runtime.liveness import LivenessMonitor, liveness_interval

CONTEXT_FACTS = "context_facts"
TRACE_CRITIQUE = "trace_critique"
SEARCH_STATE = "search_state"
REPLAY_DIGESTS = "replay_digests"
REPLAY_INTAKE = "replay_intake"
RETRIEVAL_ROUND = "retrieval_round"
DISCOVER_VERDICT = "discover_verdict"
TOOL_ACTION_GUARD = "tool_action_guard"
HELD_ITEM = "held_item"
CERTIFICATION = "certification"
TURN_TIMING = "turn_timing"
TECHNIQUE_BENCHMARK = "technique_benchmark"
RISK_ORDER = "risk_order"
NARY_PROPOSALS = "nary_proposals"
CONTEXT_COMPILE = "context_compile"
SUMMARY_RECEIPT = "summary_receipt"
INGEST_READBACK = "ingest_readback"
WALKTHROUGH = "walkthrough"
RUN_TRACE = "run_trace"
SPAN_EXPORT = "span_export"
ORG_RUNTIME = "org_runtime"
SANDBOX_LIVENESS = "sandbox_liveness"

#: The monotonic clock of the turn-timing step; a test replaces it.
CLOCK: Callable[[], int] = perf_counter_ns


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

    _bind_engine_consumers(lifecycle, request, state)
    _bind_host_consumers(lifecycle, request, state)
    return restore_step


def _before_finish(
    lifecycle: RunLifecycle,
    name: str,
    hook: Callable[[Any], Awaitable[None]],
    *,
    result_key: str | None = None,
) -> None:
    """Run ``hook`` once at the ``before_finish`` point, before ``completed``.

    A refusal there moves the run to ``failed`` through the normal edge.
    """

    async def settle(current: Any, *_rest: Any) -> None:
        if (result_key or name) not in current.consumer_results:
            await hook(current)

    lifecycle.add(name, "before_finish", settle)


def _bind_engine_consumers(lifecycle: RunLifecycle, request: Any, state: Any) -> None:
    """Steps that read or write through the engine decision port only."""
    ports, run_ref = request.ports, request.run_id

    def once(name: str, point: str, hook: Callable[[Any], Awaitable[None]]) -> None:
        async def step(current: Any, *_rest: Any) -> None:
            if name in current.consumer_results:
                return
            await hook(current)

        lifecycle.add(name, point, step)

    if lifecycle.enabled(CONTEXT_COMPILE) and (
        compile_request := _received(request, CONTEXT_COMPILE)
    ):

        async def compile_step(current: Any) -> None:
            if current.compiled_context is not None:
                return
            text = await run_context_compile(
                ports.engine_decision_port(), compile_request, run_ref=run_ref
            )
            current.compiled_context = text
            current.consumer_results[CONTEXT_COMPILE] = text
            ports.emit_run_event(
                {"type": "context_bundle_rendered", "byte_count": len(text.encode())}
            )

        once(CONTEXT_COMPILE, "before_step", compile_step)

    if lifecycle.enabled(RISK_ORDER) and (risk := _received(request, RISK_ORDER)):

        async def risk_step(current: Any) -> None:
            order = await request_historical_risk(
                ports.engine_decision_port(),
                risk["query"],
                risk["steps"],
                run_ref=run_ref,
            )
            current.consumer_results[RISK_ORDER] = order
            ports.emit_run_event(
                {
                    "type": "risk_order_applied",
                    "ordered": list(order.ordered),
                    "review": list(order.review),
                }
            )

        once(RISK_ORDER, "before_step", risk_step)

    if lifecycle.enabled(NARY_PROPOSALS) and (
        params := _received(request, NARY_PROPOSALS)
    ):

        async def proposal_step(current: Any) -> None:
            proposals = await read_nary_proposals(
                ports.engine_decision_port(), params, run_ref=run_ref
            )
            current.consumer_results[NARY_PROPOSALS] = proposals
            ports.emit_run_event(
                {"type": "nary_proposals_read", "count": len(proposals)}
            )

        once(NARY_PROPOSALS, "before_step", proposal_step)

    if lifecycle.enabled(INGEST_READBACK) and (
        readback := _received(request, INGEST_READBACK)
    ):

        async def readback_step(current: Any) -> None:
            receipt = await verify_ingest_readback(
                ports.engine_decision_port(), readback, run_ref=run_ref
            )
            current.consumer_results[INGEST_READBACK] = receipt
            ports.emit_run_event({"type": "ingest_readback_matched"})

        _before_finish(lifecycle, INGEST_READBACK, readback_step)

    if lifecycle.enabled(CERTIFICATION) and (cert := _received(request, CERTIFICATION)):

        async def certification_step(current: Any) -> None:
            attestation = await submit_certification_receipt(
                ports.engine_decision_port(),
                run_ref=run_ref,
                request=cert.get("request", {}),
                pins=cert.get("pins", {}),
                criterion_ids=cert.get("criterion_ids", ()),
            )
            current.consumer_results[CERTIFICATION] = attestation
            ports.emit_run_event(
                {"type": "certification_attested", "attestation_id": attestation}
            )

        _before_finish(lifecycle, CERTIFICATION, certification_step)

    if lifecycle.enabled(TURN_TIMING) and (timing := _received(request, TURN_TIMING)):
        started: dict[str, int] = {}

        async def timing_start(current: Any) -> None:
            try:
                started["request"] = CLOCK()
            except Exception:  # noqa: BLE001 - a clock failure omits the observation
                started.clear()

        async def timing_end(current: Any, _step: Any) -> None:
            recorded = await record_turn_timing(
                ports.engine_decision_port(),
                run_ref=run_ref,
                receipt_ref=timing["receipt_ref"],
                request_started_ns=started.get("request", current.metrics.started_ns),
                run_started_ns=current.metrics.started_ns,
                clock=CLOCK,
            )
            ports.emit_run_event({"type": "turn_timing_recorded", "recorded": recorded})

        lifecycle.add(TURN_TIMING, "before_step", timing_start)
        lifecycle.add(TURN_TIMING, "after_step", timing_end)

    if lifecycle.enabled(ORG_RUNTIME) and (org := _received(request, ORG_RUNTIME)):

        async def staffing_step(current: Any) -> None:
            port = ports.engine_decision_port()
            staffing = await staff_run(
                port, org["recruit"], org["allocate"], run_ref=run_ref
            )
            state_after = await advance_instance(
                port, org["instance_id"], "staffed", run_ref=run_ref
            )
            current.consumer_results[ORG_RUNTIME] = staffing
            ports.emit_run_event(
                {
                    "type": "staffing_applied",
                    "recruited": list(staffing.recruited),
                    "allocation": dict(staffing.allocation),
                    "instance_state": state_after,
                }
            )

        async def drain_step(current: Any) -> None:
            drained = await advance_instance(
                ports.engine_decision_port(),
                org["instance_id"],
                "drain",
                run_ref=run_ref,
            )
            current.consumer_results["org_runtime_drain"] = drained
            ports.emit_run_event(
                {"type": "instance_drained", "instance_state": drained}
            )

        once(ORG_RUNTIME, "before_step", staffing_step)
        _before_finish(
            lifecycle, ORG_RUNTIME, drain_step, result_key="org_runtime_drain"
        )

    if lifecycle.enabled(SPAN_EXPORT):
        if state.event_log is None:
            state.event_log = []

        async def span_step(current: Any) -> None:
            spans = build_spans(
                run_ref, list(current.event_log or ()), list(current.tool_trace)
            )
            digests = await export_spans(
                ports.engine_decision_port(), spans, run_ref=run_ref
            )
            current.consumer_results[SPAN_EXPORT] = digests
            ports.emit_run_event({"type": "spans_exported", "count": len(digests)})

        _before_finish(lifecycle, SPAN_EXPORT, span_step)

    if lifecycle.enabled(WALKTHROUGH) and (walk := _received(request, WALKTHROUGH)):
        if state.event_log is None:
            state.event_log = []

        async def walkthrough_step(current: Any) -> None:
            manifest = build_walkthrough_manifest(
                walk["sections"], list(current.event_log or ())
            )
            digest = await send_walkthrough_manifest(
                ports.engine_decision_port(), manifest, run_ref=run_ref
            )
            current.consumer_results[WALKTHROUGH] = manifest
            ports.emit_run_event({"type": "walkthrough_sent", "record_digest": digest})

        _before_finish(lifecycle, WALKTHROUGH, walkthrough_step)


def _bind_host_consumers(lifecycle: RunLifecycle, request: Any, state: Any) -> None:
    """Steps that reach a host port other than the engine decision port."""
    ports, run_ref = request.ports, request.run_id

    if lifecycle.enabled(HELD_ITEM) and (held := _received(request, HELD_ITEM)):

        async def held_step(current: Any) -> None:
            released = list(current.outputs)

            async def apply() -> None:
                current.outputs[:] = released

            # The output proposal stays held until the host admits it.
            current.outputs.clear()
            result = await apply_held_item(
                ports.confirm,
                apply,
                run_ref=run_ref,
                item_ref=held["item_ref"],
                item_kind=held["item_kind"],
            )
            current.consumer_results[HELD_ITEM] = result
            ports.emit_run_event(
                {
                    "type": "held_item_decided",
                    "applied": result.applied,
                    "verdict_ref": result.verdict_ref,
                }
            )

        _before_finish(lifecycle, HELD_ITEM, held_step)

    if lifecycle.enabled(SUMMARY_RECEIPT) and (
        summary := _received(request, SUMMARY_RECEIPT)
    ):

        async def summary_step(current: Any) -> None:
            if SUMMARY_RECEIPT in current.consumer_results:
                return
            output, receipt = await invoke_summary(
                ports.summary_provider,
                summary["context"],
                admission_ref=summary["admission_ref"],
                owner_requirement_id=summary["owner_requirement_id"],
            )
            current.consumer_results[SUMMARY_RECEIPT] = (output, receipt)
            ports.emit_run_event(
                {"type": "summary_receipt_minted", "receipt": dict(receipt)}
            )

        lifecycle.add(SUMMARY_RECEIPT, "before_step", summary_step)

    if lifecycle.enabled(TECHNIQUE_BENCHMARK) and (
        bench := _received(request, TECHNIQUE_BENCHMARK)
    ):

        async def benchmark_step(current: Any) -> None:
            if TECHNIQUE_BENCHMARK in current.consumer_results:
                return
            if ports.technique_measure is None:
                raise ConsumerRefused("TECHNIQUE_MEASURE_UNAVAILABLE")
            logged = await emit_technique_receipts(
                ports.engine_decision_port(),
                ports.technique_measure,
                run_ref=run_ref,
                model_id=bench["model_id"],
                config_digest=bench["config_digest"],
                techniques=tuple(bench.get("techniques", ())) or TECHNIQUES,
            )
            current.consumer_results[TECHNIQUE_BENCHMARK] = logged
            ports.emit_run_event(
                {"type": "technique_receipts_logged", "techniques": list(logged)}
            )

        lifecycle.add(TECHNIQUE_BENCHMARK, "before_step", benchmark_step)

    if lifecycle.enabled(SANDBOX_LIVENESS):
        liveness = _received(request, SANDBOX_LIVENESS) or {}
        watchers: list[asyncio.Task[None]] = []
        monitors: list[LivenessMonitor] = []

        async def liveness_start(_request: Any, _plan: Any) -> None:
            if ports.sandbox_processes is None or ports.liveness_wait is None:
                raise ConsumerRefused("liveness_unavailable")

            async def log_evidence(body: Any) -> Any:
                answer = await engine_step.record(
                    ports.engine_decision_port(), "Evaluate", body, run_ref=run_ref
                )
                return answer.payload

            monitor = LivenessMonitor(
                log_evidence,
                ports.confirm,
                ports.sandbox_processes(run_ref),
                run_id=run_ref,
                interval_s=liveness_interval(liveness.get("interval_s")),
            )
            monitors.append(monitor)
            watchers.append(asyncio.create_task(monitor.watch(ports.liveness_wait)))

        async def liveness_gate(current: Any) -> None:
            current.consumer_results[SANDBOX_LIVENESS] = True
            if monitors and monitors[0].refusal is not None:
                raise ConsumerRefused(monitors[0].refusal.code)

        async def liveness_stop(_current: Any) -> None:
            while watchers:
                task = watchers.pop()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        lifecycle.add(SANDBOX_LIVENESS, "on_start", liveness_start)
        _before_finish(lifecycle, SANDBOX_LIVENESS, liveness_gate)
        lifecycle.add(SANDBOX_LIVENESS, "on_end", liveness_stop)

    if lifecycle.enabled(RUN_TRACE):

        async def trace_step(current: Any) -> None:
            if "run_trace_sent" in current.consumer_results:
                return
            if ports.run_trace is None:
                if (
                    current.status.value == "completed"
                    or not current.status.is_terminal
                ):
                    raise ConsumerRefused("run_trace_unavailable")
                return  # a run that already ended has no result to refuse
            if any("outcome" not in step for step in current.tool_trace):
                raise ConsumerRefused("run_trace_invalid")
            current.consumer_results["run_trace_sent"] = True
            await ports.run_trace(
                {"run_id": current.run_id, "steps": list(current.tool_trace)}
            )

        async def trace_end(current: Any) -> None:
            if not current.status.value == "completed":
                await trace_step(current)

        # Completed runs deliver before ``completed``, so a refusal fails the run.
        lifecycle.add(RUN_TRACE, "before_finish", trace_step)
        lifecycle.add(RUN_TRACE, "on_end", trace_end)
