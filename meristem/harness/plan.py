"""The plan-cluster consumers of the run loop.

Each consumer reaches the decision engine only through the host-bound
:class:`meristem.host_ports.EngineDecisionPort` and applies the answer
it receives without deciding anything of its own. A consumer whose port
is unbound refuses ``engine_decision_unavailable``; a malformed
received value refuses the consumer's closed local code; an engine
refusal passes through under the engine's closed code. The run loop
fails closed with the first refusal that reaches it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol

from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import (
    EngineAnswer,
    EngineDecisionPort,
    EngineDecisionRefused,
)


def canonical_json(value: Any) -> str:
    """Canonical JSON of one received value: sorted keys, no whitespace."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _step_graph(draft: Any) -> Mapping[str, Any]:
    """The received typed step graph; every step carries its type."""
    if not isinstance(draft, Mapping):
        raise ConsumerRefused("workflow_graph_invalid")
    steps = draft.get("steps")
    if not isinstance(steps, (list, tuple)) or not steps:
        raise ConsumerRefused("workflow_graph_invalid")
    for step in steps:
        if not isinstance(step, Mapping):
            raise ConsumerRefused("workflow_graph_invalid")
        step_type = step.get("type")
        if not isinstance(step_type, str) or not step_type.strip():
            raise ConsumerRefused("workflow_graph_invalid")
    return draft


@dataclass(frozen=True, slots=True)
class CompiledWorkflow:
    """The stored workflow graph and the engine's compile record."""

    source_digest: str
    program_ref: str | None
    record: EngineAnswer


async def store_then_compile(
    draft: Mapping[str, Any],
    port: EngineDecisionPort | None,
    *,
    run_ref: str,
) -> CompiledWorkflow:
    """Send the typed step graph with its compile record to the engine.

    The harness never compiles: it sends the graph it receives inside
    one ``RunRecord`` decision-log write, which stores the body and
    records the compile in one engine call. A step without its type
    refuses ``workflow_graph_invalid`` before the call. The
    ``source_digest`` is the SHA-256 of the canonical graph bytes; the
    compiled program ref is the record digest the engine returns.
    """
    if port is None:
        raise EngineDecisionRefused("engine_decision_unavailable")
    graph = _step_graph(draft)
    graph_json = canonical_json(graph)
    source_digest = hashlib.sha256(graph_json.encode("utf-8")).hexdigest()
    record = await port.log("RunRecord", graph, run_ref=run_ref)
    if record.refusal_code is not None:
        raise ConsumerRefused(record.refusal_code)
    return CompiledWorkflow(
        source_digest=source_digest,
        program_ref=record.record_digest,
        record=record,
    )


@dataclass(frozen=True, slots=True)
class SkillWorkflowDraft:
    """The typed step graph drafted from one skill document.

    ``source_digest`` is the SHA-256 of the document bytes;
    ``program_ref`` is the compiled program record the engine commits,
    absent while the engine has not answered.
    """

    workflow: Mapping[str, Any]
    source_digest: str
    program_ref: str | None = None


async def draft_skill_workflow(
    skill_text: str,
    port: EngineDecisionPort | None,
    *,
    run_ref: str,
) -> SkillWorkflowDraft:
    """Draft the typed step graph of one skill document and submit it.

    The document is read as the received workflow graph; a step
    without its type, or a document without steps, refuses
    ``skill_draft_invalid`` before the submission. The document's
    SHA-256 digest travels with the graph through one
    ``skill_workflow_compile`` decision; the engine validates and
    stores the program and returns its record digest. Engine codes
    pass through; an unbound port refuses
    ``engine_decision_unavailable`` and decides nothing.
    """
    if port is None:
        raise EngineDecisionRefused("engine_decision_unavailable")
    if type(skill_text) is not str or not skill_text.strip():
        raise ConsumerRefused("skill_draft_invalid")
    try:
        document = json.loads(skill_text)
    except json.JSONDecodeError:
        raise ConsumerRefused("skill_draft_invalid") from None
    try:
        graph = _step_graph(document)
    except ConsumerRefused:
        # This consumer's closed local code for a malformed draft.
        raise ConsumerRefused("skill_draft_invalid") from None
    source_digest = hashlib.sha256(skill_text.encode("utf-8")).hexdigest()
    answer = await port.decide(
        "skill_workflow_compile",
        {"skill_workflow": dict(graph), "source_digest": source_digest},
        run_ref=run_ref,
    )
    if answer.refusal_code is not None:
        raise ConsumerRefused(answer.refusal_code)
    if answer.outcome not in ("solved", "committed"):
        return SkillWorkflowDraft(workflow=graph, source_digest=source_digest)
    return SkillWorkflowDraft(
        workflow=graph,
        source_digest=source_digest,
        program_ref=answer.record_digest,
    )


@dataclass(frozen=True, slots=True)
class TaskPlanSubmission:
    """The structured plan submitted after a refused route stage."""

    tasks: tuple[Mapping[str, Any], ...]
    proposal_digest: str
    plan: Mapping[str, Any] | None
    record_digest: str | None


def _extract_task_plan(model_output: Any) -> list[Mapping[str, Any]]:
    """The structured plan held in the model output, or no tasks."""
    document = model_output
    if type(document) is str:
        try:
            document = json.loads(document)
        except json.JSONDecodeError:
            return []
    if not isinstance(document, Mapping):
        return []
    raw_tasks = document.get("tasks")
    if not isinstance(raw_tasks, (list, tuple)) or not raw_tasks:
        return []
    tasks: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for task in raw_tasks:
        if not isinstance(task, Mapping):
            return []
        task_id = task.get("id")
        prompt = task.get("prompt")
        depends_on = task.get("depends_on")
        if type(task_id) is not str or not task_id or task_id in seen:
            return []
        if type(prompt) is not str or not prompt:
            return []
        if not isinstance(depends_on, (list, tuple)) or not all(
            type(dependency) is str for dependency in depends_on
        ):
            return []
        seen.add(task_id)
        tasks.append(task)
    if any(
        dependency not in seen
        for task in tasks
        for dependency in task.get("depends_on", ())
    ):
        return []
    return tasks


async def submit_task_plan_on_no_stage(
    request: Any, port: EngineDecisionPort | None, *, run_ref: str
) -> TaskPlanSubmission | None:
    """Submit a task plan when no route stage matches.

    On the engine refusal ``ROUTE_NO_STAGE`` the structured plan is
    extracted from the model output — tasks with ids, prompts and
    ``depends_on`` — and submitted once as a ``TaskPlan`` through one
    ``task_plan`` decision; the run runs the plan the answer returns.
    The harness never routes by itself. Model output that holds no
    task refuses ``plan_extraction_failed``; other engine codes pass
    through without a submission; an unbound port refuses
    ``engine_decision_unavailable`` and decides nothing. The proposal
    digest and the record digest enter the trace.
    """
    if port is None:
        raise EngineDecisionRefused("engine_decision_unavailable")
    metadata = getattr(request, "metadata", None) or {}
    route_refusal = (
        metadata.get("route_refusal") if isinstance(metadata, Mapping) else None
    )
    if route_refusal != "ROUTE_NO_STAGE":
        return None
    tasks = _extract_task_plan(metadata.get("model_output"))
    if not tasks:
        raise ConsumerRefused("plan_extraction_failed")
    proposal = {"tasks": [dict(task) for task in tasks]}
    answer = await port.decide("task_plan", proposal, run_ref=run_ref)
    if answer.refusal_code is not None:
        raise ConsumerRefused(answer.refusal_code)
    plan = (
        answer.payload.get("plan")
        if answer.outcome in ("solved", "committed")
        else None
    )
    return TaskPlanSubmission(
        tasks=tuple(dict(task) for task in tasks),
        proposal_digest=canonical_json(proposal),
        plan=dict(plan) if isinstance(plan, Mapping) else None,
        record_digest=answer.record_digest,
    )


@dataclass(frozen=True, slots=True)
class PlanningAdmission:
    """The gap proposals one planning admission answer admits.

    ``admitted`` are the proposal ids the feature set may plan;
    ``record_digest`` is the admission record the run trace carries.
    """

    admitted: tuple[str, ...]
    record_digest: str | None


async def ask_planning_admission(
    proposals: Sequence[str],
    repo: str,
    revision: str,
    port: EngineDecisionPort | None,
    *,
    run_ref: str,
) -> PlanningAdmission:
    """Ask the engine which gap proposals may be planned.

    One ``planning_admission`` decision carries the repo, the revision
    and the gap proposals; the feature set is planned only after the
    answer and only the admitted proposals. An answer that admits no
    proposal refuses ``planning_not_admitted``; an admitted id outside
    the question is malformed; engine codes pass through; an unbound
    port refuses ``engine_decision_unavailable`` and the guarded
    action does not start.
    """
    if port is None:
        raise EngineDecisionRefused("engine_decision_unavailable")
    ids = tuple(proposals)
    if not ids or any(type(proposal) is not str or not proposal for proposal in ids):
        raise ValueError("planning admission needs gap proposal ids")
    answer = await port.decide(
        "planning_admission",
        {"repo": repo, "revision": revision, "proposals": list(ids)},
        run_ref=run_ref,
    )
    if answer.refusal_code is not None:
        raise ConsumerRefused(answer.refusal_code)
    admitted_raw = (
        answer.payload.get("admitted")
        if answer.outcome in ("solved", "committed")
        else None
    )
    if not isinstance(admitted_raw, (list, tuple)) or not admitted_raw:
        raise ConsumerRefused("planning_not_admitted")
    admitted = tuple(admitted_raw)
    if any(type(proposal) is not str or proposal not in ids for proposal in admitted):
        raise ValueError("the admission admits proposals outside the question")
    return PlanningAdmission(admitted=admitted, record_digest=answer.record_digest)


_SKILL_TRANSFORMATION_KEYS = frozenset({"instructions", "steps", "model"})


def apply_skill_transformation(
    plan: Any, transformation: Mapping[str, Any] | None
) -> Any:
    """Apply the received skill transformation as received.

    The harness ranks nothing: the received values replace the plan's
    instructions, steps and model id within their closed types;
    anything the plan does not carry stays untouched. A value outside
    its closed type, or a key the plan does not carry, refuses
    ``SKILL_TRANSFORMATION_INVALID``.
    """
    if transformation is None:
        return plan
    if not isinstance(transformation, Mapping):
        raise ConsumerRefused("SKILL_TRANSFORMATION_INVALID")
    unknown = set(transformation) - _SKILL_TRANSFORMATION_KEYS
    if unknown:
        raise ConsumerRefused("SKILL_TRANSFORMATION_INVALID")
    updated = plan
    if "instructions" in transformation:
        instructions = transformation["instructions"]
        if type(instructions) is not str:
            raise ConsumerRefused("SKILL_TRANSFORMATION_INVALID")
        updated = replace(updated, instructions=instructions)
    if "steps" in transformation:
        steps = transformation["steps"]
        if (
            not isinstance(steps, (list, tuple))
            or not steps
            or any(type(step) is not str or not step for step in steps)
        ):
            raise ConsumerRefused("SKILL_TRANSFORMATION_INVALID")
        updated = replace(updated, steps=tuple(steps))
    if "model" in transformation:
        model = transformation["model"]
        if type(model) is not str or not model:
            raise ConsumerRefused("SKILL_TRANSFORMATION_INVALID")
        updated = replace(updated, model=model)
    return updated


@dataclass(frozen=True, slots=True)
class WorkflowEntry:
    """One resolved workflow from the host registry."""

    id: str
    version: str
    digest: str


class WorkflowRegistryPort(Protocol):
    """The host-bound workflow registry read before execution.

    Meristem holds no registry and does not start the workflow engine;
    the host starts it lazily and binds one reader here.
    """

    async def read(self, name: str, *, run_ref: str) -> WorkflowEntry | None:
        """One registry read; ``None`` when the name is unknown."""
        ...


class WorkflowResolutionRefused(RuntimeError):
    """A workflow that cannot resolve before the first step."""

    def __init__(self, code: str, workflow: str) -> None:
        self.code = code
        self.workflow = workflow
        super().__init__(f"{code}: {workflow}")


async def resolve_workflows(
    names: Sequence[str],
    registry_port: WorkflowRegistryPort | None,
    *,
    expected_digests: Mapping[str, str] | None = None,
    run_ref: str,
) -> tuple[WorkflowEntry, ...]:
    """Resolve each workflow through the registry before execution.

    For each name the host registry port is asked one read; the result
    must carry its id, version and digest. An unknown name refuses
    ``WORKFLOW_UNKNOWN`` and no step runs; a digest differing from the
    plan refuses ``WORKFLOW_DIGEST_MISMATCH``; an unbound port refuses
    ``WORKFLOW_REGISTRY_UNAVAILABLE`` and no step runs.
    """
    for name in names:
        if type(name) is not str or not name:
            raise ValueError("workflow names are non-empty strings")
    if not names:
        return ()
    if registry_port is None:
        raise WorkflowResolutionRefused("WORKFLOW_REGISTRY_UNAVAILABLE", names[0])
    entries: list[WorkflowEntry] = []
    for name in names:
        entry = await registry_port.read(name, run_ref=run_ref)
        if entry is None:
            raise WorkflowResolutionRefused("WORKFLOW_UNKNOWN", name)
        expected = (expected_digests or {}).get(name)
        if expected is not None and entry.digest != expected:
            raise WorkflowResolutionRefused("WORKFLOW_DIGEST_MISMATCH", name)
        entries.append(entry)
    return tuple(entries)


__all__ = [
    "CompiledWorkflow",
    "PlanningAdmission",
    "SkillWorkflowDraft",
    "TaskPlanSubmission",
    "WorkflowEntry",
    "WorkflowRegistryPort",
    "WorkflowResolutionRefused",
    "apply_skill_transformation",
    "ask_planning_admission",
    "canonical_json",
    "draft_skill_workflow",
    "resolve_workflows",
    "store_then_compile",
    "submit_task_plan_on_no_stage",
]
