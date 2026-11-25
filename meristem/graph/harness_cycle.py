"""The propose–probe–compose–persist–reflect harness evolution cycle.

CONCEPT:MERISTEM-ORCH.execution.harness-evolution-cycle

The intended tick proposes and probes candidate versions, delegates the
contrast decision, persists the adoption and reflects the lesson. The
decision and persistence steps currently refuse until the engine supplies
verifiable measurement and adoption evidence; requested probe conditions
alone do not prove what the host actually admitted.

The module holds no store, keeps no ranking and serves no operation. Every
capability it reaches is a host-injected port: the improvement-library
query, the contrast decision and the envelope writer are registered by the
host (the component versions, the lesson library and the decision live in
medulla). The candidate budget k is the candidate slot count of the
stage allocation the host carries on the run plan budget; below two slots
the cycle abstains with ``budget_below_contrast`` and keeps the current
harness. The measured contrast is the authority: the cycle never ranks
candidates of its own.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from meristem.harness.edit_engine import Edit, apply_edits
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import HostPorts

from .checkpoint import RUN_STATE_UNKNOWN, CheckpointUnavailable
from .state import RunBudget, RunPlan, RunRequest, RunStatus

#: The closed refusal codes of one tick. Anything else is not a refusal.
ABSTAIN_REASONS: Final[tuple[str, ...]] = (
    "budget_below_contrast",
    "no_candidates",
    "probe_trace_missing",
    "compose_abstention",
    "measurement_unavailable",
)


class CycleRefusal(Exception):
    """The cycle abstained with a typed code; the current harness is kept."""

    def __init__(self, code: str) -> None:
        if code not in ABSTAIN_REASONS:
            raise ValueError(f"open refusal code: {code!r}")
        self.code = code
        super().__init__(code)


#: The served improvement-library query, driven by the host: the task class
#: in, the versions it returns out, best first. Each entry carries the
#: component kind and the component version reference of one version.
LibraryQuery = Callable[[str], Sequence[Mapping[str, Any]]]


@dataclass(frozen=True)
class HarnessCandidate:
    """One proposed candidate component version for one task.

    Carries references, not content: the component version reference is the
    graph fact the host resolves, and the shared seed, model and budget
    references bind the probe conditions.
    """

    candidate_id: str
    task_ref: str
    component_kind: str
    component_version_ref: str
    seed: str
    model_ref: str
    budget_ref: str


@dataclass(frozen=True)
class CycleOutcome:
    """The outcome of one cycle tick.

    ``abstain_reason`` is the closed set :data:`ABSTAIN_REASONS`; an abstained
    tick adopts nothing and carries no contrast or lesson reference.
    """

    task_ref: str
    k: int
    adopted_version_ref: str | None
    abstain_reason: str | None
    contrast_ref: str | None
    lesson_ref: str | None

    def __post_init__(self) -> None:
        if (
            self.abstain_reason is not None
            and self.abstain_reason not in ABSTAIN_REASONS
        ):
            raise ValueError(f"open refusal code: {self.abstain_reason!r}")
        if self.adopted_version_ref is not None and self.abstain_reason is not None:
            raise ValueError("an abstained tick adopts nothing")


def propose(
    *,
    task_ref: str,
    task_class: str,
    library: LibraryQuery,
    seed: str,
    model_ref: str,
    budget_ref: str,
    k: int,
) -> tuple[HarnessCandidate, ...]:
    """Build one candidate per version the improvement library returns.

    The library is the served improvement-library query (driven by the host);
    the cycle only reads its answer for ``task_class`` and stores nothing, so
    an empty answer refuses the tick with ``no_candidates``. At most ``k``
    candidates are kept, in the library's returned order.
    """
    versions = tuple(library(task_class))
    if not versions:
        raise CycleRefusal("no_candidates")
    candidates: list[HarnessCandidate] = []
    for index, version in enumerate(versions[:k]):
        candidates.append(
            HarnessCandidate(
                candidate_id=f"{task_ref}:candidate:{index}",
                task_ref=task_ref,
                component_kind=str(version["component_kind"]),
                component_version_ref=str(version["component_version_ref"]),
                seed=seed,
                model_ref=model_ref,
                budget_ref=budget_ref,
            )
        )
    return tuple(candidates)


#: The probe id of the current-harness probe (the contrast's run a).
CURRENT_PROBE_ID = "current"


@dataclass(frozen=True)
class ProbeTrace:
    """One probe under the shared conditions: its trace and its outcome.

    ``trace_id`` is the probe run's run id; the host records the run's events
    as the trace. ``conditions_digest`` binds the shared task, seed, model and
    budget references every probe of the tick ran under.
    """

    candidate_id: str
    trace_id: str
    conditions_digest: str
    status: RunStatus
    steps: int
    usage: Mapping[str, int]
    error: str | None


def conditions_digest(*, task: str, seed: str, model_ref: str, budget_ref: str) -> str:
    """The digest that binds the shared probe conditions.

    Every probe of one tick runs the same task with the same seed, model
    reference and budget reference; the digest of those four values is what
    the probe traces carry and what the engine checks against.
    """
    payload = json.dumps(
        {"task": task, "seed": seed, "model_ref": model_ref, "budget_ref": budget_ref},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def probe_candidates(
    *,
    graph: Any,
    config: Mapping[str, Any] | None,
    task: str,
    seed: str,
    model_ref: str,
    budget_ref: str,
    run_id: str,
    current_version_ref: str,
    candidates: Sequence[HarnessCandidate],
    ports: HostPorts,
    mode: str = "ask",
    agent_name: str = "",
) -> tuple[ProbeTrace, ...]:
    """Probe the current harness and each candidate under one shared condition.

    The host must admit and materialize each probe. The requested condition
    digest is not evidence of actual execution or a measurement receipt.
    Without the host executor, refuse before starting any probe.
    """
    if any(
        (candidate.seed, candidate.model_ref, candidate.budget_ref)
        != (seed, model_ref, budget_ref)
        for candidate in candidates
    ):
        raise CycleRefusal("probe_trace_missing")
    provider = ports.harness_probe_provider()
    if provider is None:
        raise CycleRefusal("probe_trace_missing")
    digest = conditions_digest(
        task=task, seed=seed, model_ref=model_ref, budget_ref=budget_ref
    )
    entries: list[tuple[str, str]] = []
    if current_version_ref:
        entries.append((CURRENT_PROBE_ID, current_version_ref))
    entries.extend(
        (candidate.candidate_id, candidate.component_version_ref)
        for candidate in candidates
    )
    traces: list[ProbeTrace] = []
    for candidate_id, version_ref in entries:
        trace_id = f"{run_id}:probe:{candidate_id}"
        try:
            result = await provider(
                graph=graph,
                config=config,
                task=task,
                run_id=trace_id,
                mode=mode,
                requested_model_id=model_ref,
                budget_ref=budget_ref,
                agent_name=agent_name,
                metadata={
                    "harness_probe": {
                        "candidate_id": candidate_id,
                        "component_version_ref": version_ref,
                        "seed": seed,
                    }
                },
            )
        except Exception:
            raise CycleRefusal("probe_trace_missing") from None
        if not isinstance(result, Mapping) or result.get("run_id") != trace_id:
            raise CycleRefusal("probe_trace_missing")
        status = result.get("status")
        try:
            probe_status = RunStatus(status)
        except (TypeError, ValueError):
            # A value outside the closed run state machine refuses the probe
            # with the same typed code the checkpoint restore path uses.
            raise CheckpointUnavailable(
                "probe run state is unknown", code=RUN_STATE_UNKNOWN
            ) from None
        usage = result.get("usage") or {}
        traces.append(
            ProbeTrace(
                candidate_id=candidate_id,
                trace_id=trace_id,
                conditions_digest=digest,
                status=probe_status,
                steps=int(result.get("steps") or 0),
                usage={str(key): int(value) for key, value in dict(usage).items()},
                error=None if result.get("error") is None else str(result["error"]),
            )
        )
    return tuple(traces)


@dataclass(frozen=True)
class ContrastRequest:
    """The payload of one served contrast decision call per tick.

    It carries trace references only: the engine measures the retry,
    tool-selection, evaluation-score and cost deltas from the stored
    traces and answers one adoption or one abstention.
    """

    task_ref: str
    task_class: str
    conditions_digest: str
    candidate_version_refs: tuple[str, ...]
    baseline_trace_id: str
    probe_trace_ids: tuple[str, ...]


@dataclass(frozen=True)
class TraceRefs:
    """The trace references one contrast decision measures against.

    One probe of the current harness plus one probe per candidate, all
    under the tick's shared conditions digest.
    """

    baseline_trace_id: str
    candidate_version_refs: tuple[str, ...]
    probe_trace_ids: tuple[str, ...]


def collect_trace_refs(
    *,
    baseline: ProbeTrace | None,
    candidates: Sequence[HarnessCandidate],
    traces: Mapping[str, ProbeTrace],
) -> TraceRefs:
    """Collect the trace references the engine measures from.

    The request carries these ids and no delta of its own: the engine
    reads the stored traces and computes the deltas itself.
    """
    return TraceRefs(
        baseline_trace_id=baseline.trace_id if baseline is not None else "",
        candidate_version_refs=tuple(
            candidate.component_version_ref for candidate in candidates
        ),
        probe_trace_ids=tuple(
            traces[candidate.candidate_id].trace_id for candidate in candidates
        ),
    )


@dataclass(frozen=True)
class ContrastAnswer:
    """The engine's answer: one adoption or one abstention, never both.

    ``abstain_reason`` is the engine's closed vocabulary, passed through.
    On an adoption ``adopted_component_kind`` and ``adopted_content`` carry
    the adopted component's kind and content for the in-run switch, the
    content resolved by the host from the graph.
    """

    adopted_version_ref: str | None = None
    abstain_reason: str | None = None
    adopted_component_kind: str = ""
    adopted_content: str = ""


#: The served contrast decision's closed question kind.
CONTRAST_KIND = "harness_contrast"


def _field(value: Any, name: str) -> Any:
    """One duck-typed field of an answer the engine types itself."""
    return getattr(value, name, None)


async def compose(
    *,
    task_ref: str,
    task_class: str,
    conditions_digest_value: str,
    candidates: Sequence[HarnessCandidate],
    baseline: ProbeTrace | None,
    traces: Mapping[str, ProbeTrace],
    ports: HostPorts,
    run_ref: str,
) -> tuple[ContrastAnswer, str | None]:
    """Send one contrast decision to the engine with trace references only.

    The engine measures the deltas from the stored traces and answers one
    adoption or one abstention; the answer's record digest enters the
    outcome's contrast reference. An absent port or an
    ``insufficient_evidence`` refusal refuses ``measurement_unavailable``
    before the decision; an answer without an adoption refuses
    ``compose_abstention``; engine codes such as
    ``HARNESS_CONTRAST_TRACE_MISMATCH`` and
    ``HARNESS_COMPONENT_VERSION_UNKNOWN`` pass through unchanged.
    """
    engine = ports.engine_decision_port()
    if engine is None:
        raise CycleRefusal("measurement_unavailable")
    refs = collect_trace_refs(baseline=baseline, candidates=candidates, traces=traces)
    request = ContrastRequest(
        task_ref=task_ref,
        task_class=task_class,
        conditions_digest=conditions_digest_value,
        candidate_version_refs=refs.candidate_version_refs,
        baseline_trace_id=refs.baseline_trace_id,
        probe_trace_ids=refs.probe_trace_ids,
    )
    params = {
        "task_ref": request.task_ref,
        "task_class": request.task_class,
        "conditions_digest": request.conditions_digest,
        "candidate_version_refs": list(request.candidate_version_refs),
        "baseline_trace_id": request.baseline_trace_id,
        "probe_trace_ids": list(request.probe_trace_ids),
    }
    answer = await engine.decide(CONTRAST_KIND, params, run_ref=run_ref)
    if answer.refusal_code is not None:
        if answer.refusal_code == "insufficient_evidence":
            raise CycleRefusal("measurement_unavailable")
        raise ConsumerRefused(answer.refusal_code)
    adopted_ref = answer.payload.get("adopted_version_ref")
    if adopted_ref is None:
        raise CycleRefusal("compose_abstention")
    normalized = ContrastAnswer(
        adopted_version_ref=str(adopted_ref),
        abstain_reason=None,
        adopted_component_kind=str(answer.payload.get("adopted_component_kind") or ""),
        adopted_content=str(answer.payload.get("adopted_content") or ""),
    )
    return normalized, answer.record_digest


#: The generated-client envelope writer, driven by the host: one graph-fact
#: envelope in, the written fact's reference out.
EnvelopeWriter = Callable[[Mapping[str, Any]], str]


async def persist(
    *,
    task_ref: str,
    task_class: str,
    decision_record_digest: str | None,
    conditions_digest_value: str,
    adopted: HarnessCandidate,
    answer: ContrastAnswer,
    evidence_run_ids: Sequence[str],
    ports: HostPorts,
    run_ref: str,
    write_envelope: EnvelopeWriter,
    root: str | Path = ".",
) -> tuple[str, str, str]:
    """Persist the adoption through the engine decision port, then switch.

    One ``HarnessAdoption`` decision-log write carries the task, the
    decision record digest, the conditions digest, the adopted version
    and the harness-authored lesson; the engine validates and stores
    it under the run's delegated principal. On ``committed`` the
    answer's ``adoption_ref``, ``contrast_ref`` and ``lesson_ref`` are
    returned, the adoption envelope is written, and the adopted content
    is switched in under ``.harness/`` with ``apply_edits`` — after the
    commit, never before. The engine codes ``LessonWithoutEvidence``,
    ``ContrastConditionsMismatch`` and ``AdoptedHarnessNoDecision``
    pass through and leave the harness unchanged; an absent port or an
    ``insufficient_evidence`` answer refuses
    ``measurement_unavailable`` before any write or switch.
    """
    engine = ports.engine_decision_port()
    if engine is None:
        raise CycleRefusal("measurement_unavailable")
    lesson = {
        "deficiency": (
            f"the current harness for {task_class} under the shared "
            f"conditions {conditions_digest_value[:16]}"
        ),
        "situation": f"evolution tick for {task_ref}",
        "changed_component_kind": adopted.component_kind,
        "what_changed": adopted.component_version_ref,
        "applicability": task_class,
        "evidence_run_ids": list(evidence_run_ids),
    }
    write = {
        "kind": "harness_adoption",
        "task_ref": task_ref,
        "task_class": task_class,
        "decision_record_digest": decision_record_digest,
        "conditions_digest": conditions_digest_value,
        "adopted_version_ref": adopted.component_version_ref,
        "lesson": lesson,
    }
    log_answer = await engine.log("HarnessAdoption", run_ref=run_ref)
    if log_answer.refusal_code is not None:
        if log_answer.refusal_code == "insufficient_evidence":
            raise CycleRefusal("measurement_unavailable")
        raise ConsumerRefused(log_answer.refusal_code)
    if log_answer.outcome != "committed":
        raise CycleRefusal("measurement_unavailable")
    payload = log_answer.payload
    version_ref = str(payload.get("adoption_ref") or "")
    contrast_ref = str(payload.get("contrast_ref") or "")
    lesson_ref = str(payload.get("lesson_ref") or "")
    if not (version_ref and contrast_ref and lesson_ref):
        raise CycleRefusal("measurement_unavailable")
    write_envelope(write)
    edit = Edit(
        path=f".harness/{adopted.component_kind}.md",
        search="",
        replace=answer.adopted_content,
    )
    result = apply_edits([edit], root=root)
    if not result.ok:
        raise ValueError(f"the adoption switch failed: {result.failures}")
    return version_ref, contrast_ref, lesson_ref


def reflect(
    outcome: CycleOutcome,
    *,
    ports: HostPorts,
    evidence_run_ids: Sequence[str] = (),
) -> None:
    """Report one tick's outcome through the existing run event sink.

    Submits the lesson only when the compose step adopted a candidate,
    citing the evidence runs by trace id; an abstained tick reports its
    refusal code and submits no lesson. A no-op without a registered
    host sink.
    """
    ports.emit_run_event(
        {
            "type": "harness_cycle_outcome",
            "task_ref": outcome.task_ref,
            "k": outcome.k,
            "adopted_version_ref": outcome.adopted_version_ref,
            "abstain_reason": outcome.abstain_reason,
            "contrast_ref": outcome.contrast_ref,
            "lesson_ref": outcome.lesson_ref,
            "evidence_run_ids": (
                list(evidence_run_ids)
                if outcome.adopted_version_ref is not None
                else []
            ),
        }
    )


#: The candidate slot count below which the cycle abstains when the stage
#: allocation's answer carries no received minimum of its own.
FALLBACK_MIN_CANDIDATE_SLOTS = 2


def _model_ref(model: Any) -> str:
    """The model reference shared by every probe.

    The plan's model id when the host names one, else the resolved model's
    name when the host already built it.
    """
    if isinstance(model, str):
        return model
    return str(_field(model, "model_name") or "host-resolved")


def _budget_ref(budget: RunBudget) -> str:
    """The budget reference shared by every probe: the limits the run loop
    enforces for the probe, digested."""
    payload = json.dumps(
        {
            "request_limit": budget.request_limit,
            "tool_calls_limit": budget.tool_calls_limit,
            "total_tokens_limit": budget.total_tokens_limit,
            "timeout_seconds": budget.timeout_seconds,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _refused(ports: HostPorts, task_ref: str, k: int, code: str) -> CycleOutcome:
    """The abstained outcome: the current harness is kept, nothing persisted."""
    outcome = CycleOutcome(
        task_ref=task_ref,
        k=k,
        adopted_version_ref=None,
        abstain_reason=code,
        contrast_ref=None,
        lesson_ref=None,
    )
    reflect(outcome, ports=ports)
    return outcome


async def run_cycle_tick(
    request: RunRequest,
    plan: RunPlan,
    *,
    task_class: str,
    graph: Any,
    config: Mapping[str, Any] | None = None,
    library_query: LibraryQuery,
    write_envelope: EnvelopeWriter,
    root: str | Path = ".",
    mode: str = "ask",
    agent_name: str = "",
) -> CycleOutcome:
    """One propose–probe–compose–persist–reflect tick for one task.

    Reads the candidate budget k from the candidate slot count of the
    stage allocation the run decision carries on the budget. The bound
    is the allocation's received ``candidate_slots_min`` when it carries
    one, else the fallback of two slots; below the bound the cycle
    abstains with ``budget_below_contrast`` and keeps the current
    harness. Each step refuses with one typed code; a refusal persists
    nothing and the current harness is kept.
    """
    task_ref = request.run_id
    k = plan.budget.candidate_slots
    min_slots = plan.budget.candidate_slots_min
    if min_slots is None:
        min_slots = FALLBACK_MIN_CANDIDATE_SLOTS
    if k is None or k < min_slots:
        return _refused(request.ports, task_ref, k or 0, "budget_below_contrast")
    seed = str(request.metadata.get("harness_seed") or "")
    current_version_ref = str(request.metadata.get("harness_current_version_ref") or "")
    model_ref = _model_ref(plan.model)
    budget_ref = _budget_ref(plan.budget)
    try:
        candidates = propose(
            task_ref=task_ref,
            task_class=task_class,
            library=library_query,
            seed=seed,
            model_ref=model_ref,
            budget_ref=budget_ref,
            k=k,
        )
        traces = await probe_candidates(
            graph=graph,
            config=config,
            task=request.task,
            seed=seed,
            model_ref=model_ref,
            budget_ref=budget_ref,
            run_id=task_ref,
            current_version_ref=current_version_ref,
            candidates=candidates,
            ports=request.ports,
            mode=mode,
            agent_name=agent_name,
        )
        baseline = next(
            (trace for trace in traces if trace.candidate_id == CURRENT_PROBE_ID), None
        )
        by_id = {trace.candidate_id: trace for trace in traces}
        digest = traces[0].conditions_digest
        answer, contrast_record_digest = await compose(
            task_ref=task_ref,
            task_class=task_class,
            conditions_digest_value=digest,
            candidates=candidates,
            baseline=baseline,
            traces=by_id,
            ports=request.ports,
            run_ref=task_ref,
        )
        try:
            adopted = next(
                candidate
                for candidate in candidates
                if candidate.component_version_ref == answer.adopted_version_ref
            )
        except StopIteration:
            raise ValueError(
                "the engine adopted a version outside the presented candidates"
            ) from None
        adopted_trace = by_id[adopted.candidate_id].trace_id
        evidence_run_ids = (
            (baseline.trace_id, adopted_trace)
            if baseline is not None
            else (adopted_trace,)
        )
        version_ref, contrast_ref, lesson_ref = await persist(
            task_ref=task_ref,
            task_class=task_class,
            decision_record_digest=contrast_record_digest,
            conditions_digest_value=digest,
            adopted=adopted,
            answer=answer,
            evidence_run_ids=evidence_run_ids,
            ports=request.ports,
            run_ref=task_ref,
            write_envelope=write_envelope,
            root=root,
        )
        outcome = CycleOutcome(
            task_ref=task_ref,
            k=k,
            adopted_version_ref=version_ref,
            abstain_reason=None,
            contrast_ref=contrast_ref,
            lesson_ref=lesson_ref,
        )
        reflect(outcome, ports=request.ports, evidence_run_ids=evidence_run_ids)
        return outcome
    except CycleRefusal as refusal:
        return _refused(request.ports, task_ref, k, refusal.code)


async def drive_cycle_tick(
    request: RunRequest,
    plan: RunPlan,
    *,
    task_class: str,
    graph: Any,
    config: Mapping[str, Any] | None = None,
    root: str | Path = ".",
    mode: str = "ask",
) -> CycleOutcome:
    """Drive one cycle tick through the host-registered provider.

    The host registers the driver at startup; the driver owns the served
    decision and graph ports and the tick reports through the existing run
    event sink. Without a registered provider the port fails closed.
    """
    provider = request.ports.harness_cycle_provider()
    if provider is None:
        raise RuntimeError(
            "the harness cycle requires the arbora host to register a provider"
        )
    outcome = provider(
        request,
        plan,
        task_class=task_class,
        graph=graph,
        config=config,
        root=root,
        mode=mode,
    )
    if inspect.isawaitable(outcome):
        outcome = await outcome
    if not isinstance(outcome, CycleOutcome):
        raise TypeError("the harness cycle provider must return a CycleOutcome")
    return outcome


__all__ = [
    "ABSTAIN_REASONS",
    "CONTRAST_KIND",
    "CURRENT_PROBE_ID",
    "ContrastAnswer",
    "ContrastRequest",
    "CycleOutcome",
    "CycleRefusal",
    "EnvelopeWriter",
    "FALLBACK_MIN_CANDIDATE_SLOTS",
    "HarnessCandidate",
    "LibraryQuery",
    "ProbeTrace",
    "TraceRefs",
    "collect_trace_refs",
    "conditions_digest",
    "compose",
    "drive_cycle_tick",
    "persist",
    "probe_candidates",
    "propose",
    "reflect",
    "run_cycle_tick",
]
