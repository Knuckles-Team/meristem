"""Run-local scaffold tool, request-only injection and the plan-runtime
consumers.

Each plan-runtime consumer reaches the decision engine only through the
host-bound :class:`meristem.host_ports.EngineDecisionPort` and applies the
answer it receives without deciding anything of its own. A consumer whose
port is unbound refuses ``engine_decision_unavailable``; a malformed
received value refuses the consumer's closed local code; an engine refusal
passes through under the engine's closed code. A consumer runs only when
its name is listed in ``HarnessConfig.consumers``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, cast

from pydantic import TypeAdapter, ValidationError
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, WrapRunHandler
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelRequestPart,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.run import AgentRunResult
from pydantic_ai.toolsets import FunctionToolset

from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import (
    EngineAnswer,
    EngineDecisionPort,
    EngineDecisionRefused,
    PlanItem,
    PlanState,
    ScaffoldPublisher,
)

_ITEMS = TypeAdapter(list[PlanItem])


def _without_request_payload(part: ModelRequestPart) -> ModelRequestPart:
    # Upstream split the call/return part types (tool-search, capability-load)
    # into a union with typed payloads; ``update_plan`` is always a plain
    # function-tool part, so replace through ``Any``.
    if isinstance(part, ToolReturnPart) and part.tool_name == "update_plan":
        return replace(cast(Any, part), content={"status": "scaffold_observation"})
    if isinstance(part, RetryPromptPart) and part.tool_name == "update_plan":
        return replace(part, content="Invalid scaffold update.")
    return part


def _without_scaffold_payload(messages: list[ModelMessage]) -> list[ModelMessage]:
    """Retain tool pairing while dropping scaffold arguments and observations."""
    cleaned: list[ModelMessage] = []
    for message in messages:
        if isinstance(message, ModelRequest):
            parts = [
                _without_request_payload(part)
                for part in message.parts
                if not (
                    isinstance(part, UserPromptPart)
                    and isinstance(part.content, str)
                    and part.content.startswith("Current run plan:")
                )
            ]
            # A request containing only the prior ephemeral rendering has no
            # durable meaning. Drop it instead of leaving an empty history turn.
            if not parts:
                continue
            cleaned.append(
                replace(
                    message,
                    parts=parts,
                )
            )
        else:
            response_parts = [
                replace(cast(Any, part), args={})
                if isinstance(part, ToolCallPart) and part.tool_name == "update_plan"
                else part
                for part in message.parts
            ]
            cleaned.append(replace(message, parts=response_parts))
    return cleaned


def _without_plan_injections(messages: list[ModelMessage]) -> list[ModelMessage]:
    """Remove only prior ephemeral renderings from an outgoing request.

    The current tool return is still part of the model's immediate request and
    must remain visible. ``_without_scaffold_payload`` is reserved for the
    durable run history, where both the plan arguments and observations are
    redacted.
    """
    cleaned: list[ModelMessage] = []
    for message in messages:
        if not isinstance(message, ModelRequest):
            cleaned.append(message)
            continue
        parts = [
            part
            for part in message.parts
            if not (
                isinstance(part, UserPromptPart)
                and isinstance(part.content, str)
                and part.content.startswith("Current run plan:")
            )
        ]
        if parts:
            cleaned.append(replace(message, parts=parts))
    return cleaned


class PlanScaffold(AbstractCapability[Any]):
    """One scaffold shared by a run's update tool and outgoing request hook."""

    def __init__(
        self, state: PlanState, publisher: ScaffoldPublisher | None = None
    ) -> None:
        self.state = state
        self.publisher = publisher
        self._history: list[ModelMessage] | None = None

    async def for_run(self, ctx: RunContext[Any]) -> PlanScaffold:
        """Keep cleanup tracking local to each agent call, sharing its run scaffold."""
        return PlanScaffold(self.state, self.publisher)

    def get_toolset(self) -> FunctionToolset[Any]:
        toolset: FunctionToolset[Any] = FunctionToolset()
        toolset.add_function(self.update_plan, sequential=True)
        return toolset

    async def update_plan(self, items: Any) -> dict[str, Any]:
        """Replace the whole plan with ordered text/status items.

        Status must be pending, in_progress or done. This tool does not select
        agents, skills, tools or models.
        """
        try:
            values = _ITEMS.validate_python(items, strict=True)
            revision, current = self.state.replace_versioned(values)
        except ValidationError:
            return {"status": "invalid_plan"}
        if self.publisher is not None:
            await self.publisher.publish(revision, current)
        return {"status": "updated", "items": [item.model_dump() for item in current]}

    async def before_model_request(
        self, ctx: RunContext[Any], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        # Keep the real tool observation in this outgoing request only. Replacing
        # parts instead of mutating them leaves that request's shallow copy intact.
        self._history = ctx.messages
        ctx.messages[:] = _without_scaffold_payload(ctx.messages)
        rendered = json.dumps([item.model_dump() for item in self.state.items])
        injection = ModelRequest(
            parts=[UserPromptPart(f"Current run plan: {rendered}")]
        )
        # Assignment through replace is request-only. Appending to the framework's
        # special request list would also append to persistent history.
        request_messages = _without_plan_injections(list(request_context.messages))
        return replace(request_context, messages=[*request_messages, injection])

    async def wrap_run(
        self, ctx: RunContext[Any], *, handler: WrapRunHandler
    ) -> AgentRunResult[Any]:
        try:
            return await handler()
        finally:
            if self._history is not None:
                self._history[:] = _without_scaffold_payload(self._history)
                self._history = None


def _canonical_digest(value: Any) -> str:
    """SHA-256 of one received value as canonical JSON, 64 lowercase hex."""
    canonical = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CompiledPartition:
    """The engine's selected partition, applied exactly as received."""

    selected: tuple[str, ...]
    omitted: tuple[str, ...]
    content: tuple[tuple[str, str], ...]
    record_digest: str | None


async def compile_partition(
    messages: Mapping[str, str],
    sources: Sequence[str],
    port: EngineDecisionPort | None,
    *,
    run_ref: str,
) -> CompiledPartition:
    """Compile the context from the engine's selected partition.

    The visible source ids go to one ``context_budget`` decision; the
    harness keeps only the content the answer selects and carries each
    source id as a citation. A source in neither list, or in both, is a
    defect of the answer that refuses ``partition_incomplete``. An
    unbound port refuses ``engine_decision_unavailable``; engine codes
    pass through.
    """
    if port is None:
        raise EngineDecisionRefused("engine_decision_unavailable")
    ids = tuple(str(source) for source in sources)
    if any(not source for source in ids) or len(set(ids)) != len(ids):
        raise ValueError("partition sources are distinct non-empty ids")
    if not set(ids) <= set(messages):
        raise ValueError("the partition needs the visible content of every source")
    answer = await port.decide(
        "context_budget", {"sources": list(ids)}, run_ref=run_ref
    )
    if answer.refusal_code is not None:
        raise ConsumerRefused(answer.refusal_code)
    payload = answer.payload if answer.outcome in ("solved", "committed") else {}
    selected_raw = payload.get("selected")
    omitted_raw = payload.get("omitted")
    if not isinstance(selected_raw, (list, tuple)) or not isinstance(
        omitted_raw, (list, tuple)
    ):
        raise ConsumerRefused("partition_incomplete")
    selected = tuple(str(source) for source in selected_raw)
    omitted = tuple(str(source) for source in omitted_raw)
    if set(selected) & set(omitted) or set(selected) | set(omitted) != set(ids):
        raise ConsumerRefused("partition_incomplete")
    return CompiledPartition(
        selected=selected,
        omitted=omitted,
        content=tuple((source, messages[source]) for source in selected),
        record_digest=answer.record_digest,
    )


@dataclass(frozen=True, slots=True)
class BoundaryRequest:
    """One boundary's Decide request, built from its declared params only.

    ``param_names`` are the sorted names the trace records beside the
    route record digest the engine returns.
    """

    params: Mapping[str, Any]
    param_names: tuple[str, ...]


def boundary_params(step: Mapping[str, Any], declared: set[str]) -> BoundaryRequest:
    """Build one boundary's Decide request from only its declared params.

    The boundary declares which run params it may see; every other run
    param stays out of the request. A param the step sends that the
    boundary does not declare refuses
    ``boundary_param_undeclared``; the sent param names are recorded
    sorted.
    """
    for name in step:
        if type(name) is not str or not name:
            raise ValueError("boundary param names are non-empty strings")
    if set(step) - set(declared):
        raise ConsumerRefused("boundary_param_undeclared")
    sent = {name: step[name] for name in sorted(step)}
    return BoundaryRequest(params=sent, param_names=tuple(sent))


@dataclass(frozen=True, slots=True)
class StepVerdict:
    """The engine's step verdict and its record link for the trace."""

    verdict: str
    record_digest: str | None


async def verify_step(
    state: Any, step: Any, port: EngineDecisionPort | None, *, run_ref: str
) -> StepVerdict:
    """Continue only on the engine's ``verified`` verdict.

    The step id, the digests of the step's input and output and the
    evidence ids go to one ``step_verification`` decision. Any verdict
    other than ``verified`` refuses ``step_unverified``; engine codes
    pass through. The record digest links the step event to the record.
    """
    if port is None:
        raise EngineDecisionRefused("engine_decision_unavailable")
    outputs = list(state.outputs)
    prior, output = outputs[:-1], outputs[-1] if outputs else ""
    answer = await port.decide(
        "step_verification",
        {
            "step_id": str(step),
            "input_digest": _canonical_digest(prior),
            "output_digest": hashlib.sha256(output.encode("utf-8")).hexdigest(),
            "evidence_ids": [
                hashlib.sha256(text.encode("utf-8")).hexdigest() for text in prior
            ],
        },
        run_ref=run_ref,
    )
    if answer.refusal_code is not None:
        raise ConsumerRefused(answer.refusal_code)
    verdict = (
        answer.payload.get("verdict")
        if answer.outcome in ("solved", "committed")
        else None
    )
    if verdict != "verified":
        raise ConsumerRefused("step_unverified")
    return StepVerdict(verdict=str(verdict), record_digest=answer.record_digest)


@dataclass(frozen=True, slots=True)
class CouncilPosition:
    """One persona's position with its full producer identity."""

    council_id: str
    topology_revision: str
    position_id: str
    persona_id: str
    persona_revision: str
    option_id: str
    claim_digest: str
    evidence_refs: tuple[str, ...] = ()
    constraint_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CouncilDecision:
    """The engine's accepted council positions and the decision record."""

    positions_digest: str
    accepted: tuple[str, ...]
    decision_digest: str | None


async def submit_council(
    positions: Sequence[CouncilPosition],
    port: EngineDecisionPort | None,
    *,
    run_ref: str,
) -> CouncilDecision:
    """Send the council positions with producer identity in one decision.

    All positions travel in one ``council_coordination`` decision; the
    harness applies the accepted ids of the answer only and never
    resolves a conflict. ``COUNCIL_NO_QUORUM``,
    ``COUNCIL_UNRESOLVED_CONFLICT`` and the other ``COUNCIL_*`` engine
    codes pass through. The positions digest and the decision digest
    enter the trace.
    """
    if port is None:
        raise EngineDecisionRefused("engine_decision_unavailable")
    if not positions:
        raise ValueError("council positions need at least one persona")
    wire = [
        {
            "council_id": position.council_id,
            "topology_revision": position.topology_revision,
            "position_id": position.position_id,
            "persona_id": position.persona_id,
            "persona_revision": position.persona_revision,
            "option_id": position.option_id,
            "claim_digest": position.claim_digest,
            "evidence_refs": list(position.evidence_refs),
            "constraint_refs": list(position.constraint_refs),
        }
        for position in positions
    ]
    answer = await port.decide(
        "council_coordination", {"positions": wire}, run_ref=run_ref
    )
    if answer.refusal_code is not None:
        raise ConsumerRefused(answer.refusal_code)
    accepted_raw = (
        answer.payload.get("accepted")
        if answer.outcome in ("solved", "committed")
        else None
    )
    accepted = (
        tuple(str(position) for position in accepted_raw)
        if isinstance(accepted_raw, (list, tuple))
        else ()
    )
    if any(
        position_id not in {p.position_id for p in positions}
        for position_id in accepted
    ):
        raise ValueError("the council admits positions outside the question")
    return CouncilDecision(
        positions_digest=_canonical_digest(wire),
        accepted=accepted,
        decision_digest=answer.record_digest,
    )


@dataclass(frozen=True, slots=True)
class ChosenUnit:
    """One chosen context unit with the citation of the sent unit."""

    unit_id: str
    resolution: Any
    source: str
    span: str
    revision: str


@dataclass(frozen=True, slots=True)
class ContextPackage:
    """The engine's chosen package, applied exactly as returned."""

    chosen: tuple[ChosenUnit, ...]
    record_digest: str | None


async def compile_context_package(
    evidence_units: Sequence[Mapping[str, Any]],
    budget_tokens: int,
    port: EngineDecisionPort | None,
    *,
    run_ref: str,
    cache: dict[str, ContextPackage] | None = None,
) -> ContextPackage:
    """Apply the chosen context package exactly as the engine returned it.

    The sent units and the budget go to one ``context_package``
    decision; the chosen unit ids and their resolutions are applied
    without reranking or substitution, keeping the source, span and
    revision citation of every unit. The answer is cached by the
    digest of the sent units and the budget: a changed unit or budget
    makes a new call. A chosen id outside the sent units refuses
    ``package_unit_unknown``; engine codes pass through.
    """
    if port is None:
        raise EngineDecisionRefused("engine_decision_unavailable")
    units: dict[str, Mapping[str, Any]] = {}
    for unit in evidence_units:
        unit_id = unit.get("id")
        if type(unit_id) is not str or not unit_id:
            raise ValueError("evidence units need non-empty string ids")
        if unit_id in units:
            raise ValueError("evidence unit ids repeat")
        units[unit_id] = unit
    if type(budget_tokens) is not int or budget_tokens < 0:
        raise ValueError("the context budget is a non-negative token count")
    key = _canonical_digest(
        {
            "units": [dict(unit) for unit in units.values()],
            "budget_tokens": budget_tokens,
        }
    )
    if cache is not None and key in cache:
        return cache[key]
    answer = await port.decide(
        "context_package",
        {
            "evidence_units": [dict(unit) for unit in units.values()],
            "budget_tokens": budget_tokens,
        },
        run_ref=run_ref,
    )
    if answer.refusal_code is not None:
        raise ConsumerRefused(answer.refusal_code)
    chosen_raw = (
        answer.payload.get("chosen")
        if answer.outcome in ("solved", "committed")
        else None
    )
    if not isinstance(chosen_raw, (list, tuple)):
        raise ConsumerRefused("package_unit_unknown")
    chosen: list[ChosenUnit] = []
    for entry in chosen_raw:
        if not isinstance(entry, Mapping):
            raise ConsumerRefused("package_unit_unknown")
        unit_id = entry.get("id")
        if type(unit_id) is not str or unit_id not in units:
            raise ConsumerRefused("package_unit_unknown")
        sent = units[unit_id]
        chosen.append(
            ChosenUnit(
                unit_id=unit_id,
                resolution=entry.get("resolution"),
                source=str(sent.get("source", "")),
                span=str(sent.get("span", "")),
                revision=str(sent.get("revision", "")),
            )
        )
    package = ContextPackage(chosen=tuple(chosen), record_digest=answer.record_digest)
    if cache is not None:
        cache[key] = package
    return package


@dataclass(frozen=True, slots=True)
class ContextReplacement:
    """The sealed verdict over one paired held-out query set."""

    replaced: bool
    result: str
    verdict_digest: str
    record_digest: str | None


async def consider_context_replacement(
    raw: Mapping[str, Any],
    candidate: Mapping[str, Any],
    queries: Sequence[str],
    port: EngineDecisionPort | None,
    *,
    run_ref: str,
) -> ContextReplacement:
    """Replace the active context only on a sealed ``replace`` result.

    The held-out queries were run on the raw and the candidate context
    before this step; the paired verdict set goes to one
    ``context_replacement`` decision. Only the sealed ``replace``
    result replaces the active context; any other result keeps the raw
    revision. A query missing one side refuses
    ``paired_set_incomplete``; engine codes pass through. The
    verdict-set digest and the result enter the trace.
    """
    if port is None:
        raise EngineDecisionRefused("engine_decision_unavailable")
    query_ids = tuple(str(query) for query in queries)
    if any(not query for query in query_ids) or len(set(query_ids)) != len(query_ids):
        raise ValueError("held-out queries are distinct non-empty ids")
    paired: dict[str, Any] = {}
    for query in query_ids:
        raw_side = raw.get(query) if isinstance(raw, Mapping) else None
        candidate_side = (
            candidate.get(query) if isinstance(candidate, Mapping) else None
        )
        if raw_side is None or candidate_side is None:
            raise ConsumerRefused("paired_set_incomplete")
        paired[query] = {"raw": raw_side, "candidate": candidate_side}
    answer = await port.decide(
        "context_replacement", {"paired": paired}, run_ref=run_ref
    )
    if answer.refusal_code is not None:
        raise ConsumerRefused(answer.refusal_code)
    result = (
        str(answer.payload.get("result", ""))
        if answer.outcome in ("solved", "committed")
        else ""
    )
    return ContextReplacement(
        replaced=result == "replace",
        result=result if result else "keep",
        verdict_digest=_canonical_digest(paired),
        record_digest=answer.record_digest,
    )


@dataclass(frozen=True, slots=True)
class SkillOrder:
    """One skill in its try order for a task."""

    skill_id: str
    rank: int
    attempt: int


@dataclass(frozen=True, slots=True)
class TaskSkillOrder:
    """The try order of one task's ranked skills, parents before children."""

    task_id: str
    skills: tuple[SkillOrder, ...]


def order_skills(task_plan_answer: EngineAnswer) -> tuple[TaskSkillOrder, ...]:
    """Order the sealed skill trees for the try.

    Each task's ranked skills are tried in rank order with a parent
    skill tried before its children; a parent the rank puts after a
    child moves ahead of that child without any rank changing. The
    harness never re-ranks. Parent links that form a cycle refuse
    ``skill_tree_cycle``; engine codes pass through. Each tried skill
    id with its rank enters the trace.
    """
    if task_plan_answer.refusal_code is not None:
        raise ConsumerRefused(task_plan_answer.refusal_code)
    trees = task_plan_answer.payload.get("skill_trees")
    if trees is None:
        return ()
    if not isinstance(trees, Mapping):
        raise ValueError("skill trees are keyed by task id")
    orders: list[TaskSkillOrder] = []
    for task_id, skills in trees.items():
        if type(task_id) is not str or not task_id:
            raise ValueError("skill trees are keyed by task id")
        if not isinstance(skills, (list, tuple)):
            raise ValueError("skill trees carry ordered skill lists")
        ranked: list[tuple[str, int, str | None]] = []
        seen: set[str] = set()
        for skill in skills:
            if not isinstance(skill, Mapping):
                raise ValueError("skill tree entries are mappings")
            skill_id = skill.get("skill_id")
            rank = skill.get("rank")
            parent = skill.get("parent_skill_id")
            if type(skill_id) is not str or not skill_id:
                raise ValueError("skill tree entries need skill ids")
            if type(rank) is not int:
                raise ValueError("skill tree entries need integer ranks")
            if parent is not None and (type(parent) is not str or not parent):
                raise ValueError("skill tree parent links are skill ids")
            if skill_id in seen:
                raise ValueError("skill tree skill ids repeat")
            seen.add(skill_id)
            ranked.append((skill_id, rank, parent))
        by_id = {skill_id: (rank, parent) for skill_id, rank, parent in ranked}
        children: dict[str, list[str]] = {}
        depth: dict[str, int] = {skill_id: 0 for skill_id, _, _ in ranked}
        for skill_id, _, parent in ranked:
            if parent is not None and parent in by_id:
                children.setdefault(parent, []).append(skill_id)
                depth[skill_id] += 1
        ready = [
            (rank, skill_id)
            for skill_id, (rank, _) in by_id.items()
            if depth[skill_id] == 0
        ]
        ordered: list[str] = []
        while ready:
            ready.sort()
            _, skill_id = ready.pop(0)
            ordered.append(skill_id)
            for child in children.get(skill_id, ()):
                depth[child] -= 1
                if depth[child] == 0:
                    ready.append((by_id[child][0], child))
        if len(ordered) != len(ranked):
            raise ConsumerRefused("skill_tree_cycle")
        orders.append(
            TaskSkillOrder(
                task_id=task_id,
                skills=tuple(
                    SkillOrder(
                        skill_id=skill_id,
                        rank=by_id[skill_id][0],
                        attempt=attempt,
                    )
                    for attempt, skill_id in enumerate(ordered)
                ),
            )
        )
    return tuple(orders)


_U64_MAX = 2**64 - 1


@dataclass(frozen=True, slots=True)
class HorizonCheckpoint:
    """One horizon's credit checkpoint as the run sends it."""

    plan_digest: str
    horizon_index: int
    consumed: int
    reserved: int
    remaining: int
    ceiling: int
    rule: str
    record_digest: str | None


async def run_horizons(
    plan_digest: str,
    partition: Sequence[Mapping[str, Any]],
    port: EngineDecisionPort | None,
    *,
    run_ref: str,
) -> tuple[HorizonCheckpoint, ...]:
    """Run the plan horizon by horizon and checkpoint each one.

    Each horizon end sends its ``HorizonCheckpoint`` — plan digest,
    horizon index, consumed, reserved, remaining, ceiling and rule —
    through one ``checkpoint`` decision-log write. ``remaining`` equals
    ceiling minus consumed minus reserved in ``u64``; an overflow
    refuses ``checkpoint_overflow``. A refused checkpoint stops the
    run; engine codes pass through. One checkpoint per horizon enters
    the trace.
    """
    if port is None:
        raise EngineDecisionRefused("engine_decision_unavailable")
    if type(plan_digest) is not str or not plan_digest:
        raise ValueError("the plan digest is a non-empty string")
    checkpoints: list[HorizonCheckpoint] = []
    for index, horizon in enumerate(partition):
        if not isinstance(horizon, Mapping):
            raise ValueError("horizons are mappings")
        values: dict[str, int] = {}
        for name in ("consumed", "reserved", "ceiling"):
            value = horizon.get(name, 0)
            if type(value) is not int or value < 0 or value > _U64_MAX:
                raise ConsumerRefused("checkpoint_overflow")
            values[name] = value
        rule = horizon.get("rule", "")
        if type(rule) is not str:
            raise ValueError("the horizon rule is a string")
        consumed, reserved, ceiling = (
            values["consumed"],
            values["reserved"],
            values["ceiling"],
        )
        if consumed + reserved > ceiling:
            raise ConsumerRefused("checkpoint_overflow")
        answer = await port.log(
            "checkpoint",
            {
                "plan_digest": plan_digest,
                "horizon_index": index,
                "consumed": consumed,
                "reserved": reserved,
                "ceiling": ceiling,
                "rule": rule,
            },
            run_ref=run_ref,
        )
        if answer.refusal_code is not None:
            raise ConsumerRefused(answer.refusal_code)
        checkpoints.append(
            HorizonCheckpoint(
                plan_digest=plan_digest,
                horizon_index=index,
                consumed=consumed,
                reserved=reserved,
                remaining=ceiling - consumed - reserved,
                ceiling=ceiling,
                rule=rule,
                record_digest=answer.record_digest,
            )
        )
    return tuple(checkpoints)


@dataclass(frozen=True, slots=True)
class BranchOutcome:
    """One branch's outcome, or its named gap, as the plan names it."""

    branch_id: str
    plan_digest: str
    outcome: Mapping[str, Any] | None = None
    gap: str | None = None

    def __post_init__(self) -> None:
        if self.outcome is None and self.gap is None:
            raise ValueError("a branch carries an outcome or a named gap")
        if self.outcome is not None and self.gap is not None:
            raise ValueError("a branch carries an outcome or a named gap, not both")


@dataclass(frozen=True, slots=True)
class BranchSubmission:
    """One submitted branch outcome and its fusion receipt."""

    branch_id: str
    plan_digest: str
    record_digest: str | None


async def submit_branch_outcomes(
    branches: Sequence[BranchOutcome],
    port: EngineDecisionPort | None,
    *,
    run_ref: str,
) -> tuple[BranchSubmission, ...]:
    """Submit every branch outcome, or its named gap, for fusion.

    Each named branch travels through one ``RunRecord`` decision-log
    write; a branch that never ran is submitted as its named gap, not
    omitted. The answer's record digest is the fusion receipt the run
    applies. Engine codes pass through; one branch id and plan digest
    per submission enter the trace.
    """
    if port is None:
        raise EngineDecisionRefused("engine_decision_unavailable")
    submissions: list[BranchSubmission] = []
    for branch in branches:
        if type(branch.branch_id) is not str or not branch.branch_id:
            raise ValueError("branches need non-empty ids")
        if branch.gap is not None and (type(branch.gap) is not str or not branch.gap):
            raise ValueError("branch gaps are named")
        answer = await port.log(
            "RunRecord",
            {
                "branch_id": branch.branch_id,
                "plan_digest": branch.plan_digest,
                "gap": branch.gap,
            },
            run_ref=run_ref,
        )
        if answer.refusal_code is not None:
            raise ConsumerRefused(answer.refusal_code)
        submissions.append(
            BranchSubmission(
                branch_id=branch.branch_id,
                plan_digest=branch.plan_digest,
                record_digest=answer.record_digest,
            )
        )
    return tuple(submissions)


@dataclass(frozen=True, slots=True)
class InsightSubmission:
    """One submitted insight and its record link for the trace."""

    insight_id: str
    context_signature: str
    record_digest: str | None


async def submit_insight(
    state: Any, port: EngineDecisionPort | None, *, run_ref: str
) -> tuple[InsightSubmission, ...]:
    """Submit each observed insight at run end.

    Every insight the run observed travels through one ``RunRecord``
    decision-log write with its context signature, insight id and
    source run ids. An insight without a source run refuses
    ``insight_without_source``. Engine codes pass through; each
    insight id and its source run ids enter the trace.
    """
    if port is None:
        raise EngineDecisionRefused("engine_decision_unavailable")
    submissions: list[InsightSubmission] = []
    for raw in state.insights:
        if not isinstance(raw, Mapping):
            raise ValueError("observed insights are mappings")
        insight_id = raw.get("insight_id")
        if type(insight_id) is not str or not insight_id:
            raise ValueError("observed insights need ids")
        source_run_ids = tuple(str(run_id) for run_id in raw.get("source_run_ids", ()))
        if not source_run_ids or any(not run_id for run_id in source_run_ids):
            raise ConsumerRefused("insight_without_source")
        answer = await port.log(
            "RunRecord",
            {
                "insight_id": insight_id,
                "context_signature": str(raw.get("context_signature", "")),
                "source_run_ids": list(source_run_ids),
            },
            run_ref=run_ref,
        )
        if answer.refusal_code is not None:
            raise ConsumerRefused(answer.refusal_code)
        submissions.append(
            InsightSubmission(
                insight_id=insight_id,
                context_signature=str(raw.get("context_signature", "")),
                record_digest=answer.record_digest,
            )
        )
    return tuple(submissions)


async def apply_selected_insights(
    context: Sequence[Mapping[str, Any]],
    port: EngineDecisionPort | None,
    *,
    run_ref: str,
) -> tuple[Mapping[str, Any], ...]:
    """Add the selected insights to the model context in answer order.

    The context signature goes to one ``insight_selection`` decision;
    the insights the answer selects are appended in answer order. The
    harness adds nothing the answer does not select. Engine codes pass
    through; an unbound port refuses ``engine_decision_unavailable``
    and decides nothing.
    """
    if port is None:
        raise EngineDecisionRefused("engine_decision_unavailable")
    signature = _canonical_digest([dict(item) for item in context])
    answer = await port.decide(
        "insight_selection", {"context_signature": signature}, run_ref=run_ref
    )
    if answer.refusal_code is not None:
        raise ConsumerRefused(answer.refusal_code)
    selected = (
        answer.payload.get("selected")
        if answer.outcome in ("solved", "committed")
        else None
    )
    if selected is None:
        return tuple(context)
    if not isinstance(selected, (list, tuple)) or not all(
        isinstance(item, Mapping) for item in selected
    ):
        raise ValueError("selected insights are mappings")
    return (*tuple(context), *tuple(selected))


def record_injected_insights(
    selected: Sequence[Mapping[str, Any]], state: Any
) -> Mapping[str, Any]:
    """Record one ``insights_injected`` run event.

    After the context compile, one run event lists each insight that
    entered the model context, in injection order, as
    ``insight:id=<id>;sources=<run ids>;match_q32=<n>``. The loop
    carries the event to the run event sink.
    """
    entries: list[str] = []
    for item in selected:
        if not isinstance(item, Mapping):
            raise ValueError("injected insights are mappings")
        insight_id = item.get("insight_id")
        if type(insight_id) is not str or not insight_id:
            raise ValueError("injected insights need ids")
        sources = tuple(str(run_id) for run_id in item.get("sources", ()))
        if any(not run_id for run_id in sources):
            raise ValueError("injected insight sources are non-empty run ids")
        try:
            match_q32 = int(item.get("match_q32", 0))
        except (TypeError, ValueError):
            raise ValueError("match_q32 is an integer") from None
        entries.append(
            f"insight:id={insight_id};sources={','.join(sources)};match_q32={match_q32}"
        )
    sequence = state.event_sequence
    state.event_sequence += 1
    return {
        "type": "insights_injected",
        "run_id": state.run_id,
        "sequence": sequence,
        "insights": entries,
    }


def _hierarchy_depth(nodes: Sequence[Mapping[str, Any]]) -> int:
    """The depth of one subgoal hierarchy, ided nodes only."""
    depth = 0
    for node in nodes:
        children = node.get("children") or ()
        if not isinstance(children, (list, tuple)) or not all(
            isinstance(child, Mapping)
            and type(child.get("id")) is str
            and child.get("id")
            for child in children
        ):
            raise ValueError("hierarchy nodes need ids")
        depth = max(depth, 1 + _hierarchy_depth(children))
    return depth


@dataclass(frozen=True, slots=True)
class CurriculumDecision:
    """The engine's step rewards and its promotion verdict."""

    rewards: Mapping[str, Any]
    verdict: str
    promoted: bool
    record_digest: str | None
    promotion_digest: str | None


async def submit_curriculum(
    state: Any, port: EngineDecisionPort | None, *, run_ref: str
) -> CurriculumDecision:
    """Send the subgoal hierarchy and apply the curriculum verdict.

    The task's subgoal hierarchy and checkpoints travel with the
    trajectory through one ``reward_decomposition`` decision, and the
    step rewards through one ``curriculum_promotion`` decision. The
    verdict applies to the next task horizon; the harness never decides
    promotion locally. A hierarchy deeper than the received depth
    refuses ``hierarchy_too_deep``; engine codes pass through. Step
    rewards and the verdict enter the trace.
    """
    if port is None:
        raise EngineDecisionRefused("engine_decision_unavailable")
    curriculum = state.curriculum
    if not isinstance(curriculum, Mapping):
        raise ValueError("submit_curriculum needs the task hierarchy")
    hierarchy = curriculum.get("hierarchy")
    if not isinstance(hierarchy, (list, tuple)) or not all(
        isinstance(node, Mapping) and type(node.get("id")) is str and node.get("id")
        for node in hierarchy
    ):
        raise ValueError("the subgoal hierarchy needs ided nodes")
    depth = _hierarchy_depth(hierarchy)
    received_depth = curriculum.get("depth")
    if type(received_depth) is int and depth > received_depth:
        raise ConsumerRefused("hierarchy_too_deep")
    checkpoints = curriculum.get("checkpoints") or ()
    trajectory = curriculum.get("trajectory") or ()
    answer = await port.decide(
        "reward_decomposition",
        {
            "hierarchy": list(hierarchy),
            "checkpoints": list(checkpoints),
            "trajectory": list(trajectory),
        },
        run_ref=run_ref,
    )
    if answer.refusal_code is not None:
        raise ConsumerRefused(answer.refusal_code)
    rewards = answer.payload if answer.outcome in ("solved", "committed") else {}
    promotion_params: dict[str, Any] = {"rewards": dict(rewards)}
    window_runs = curriculum.get("window_runs")
    if window_runs is not None:
        promotion_params["window_runs"] = window_runs
    promotion = await port.decide(
        "curriculum_promotion", promotion_params, run_ref=run_ref
    )
    if promotion.refusal_code is not None:
        raise ConsumerRefused(promotion.refusal_code)
    verdict = (
        str(promotion.payload.get("verdict", ""))
        if promotion.outcome in ("solved", "committed")
        else "abstained"
    )
    return CurriculumDecision(
        rewards=rewards,
        verdict=verdict,
        promoted=verdict == "promote",
        record_digest=answer.record_digest,
        promotion_digest=promotion.record_digest,
    )


@dataclass(frozen=True, slots=True)
class FallbackWindow:
    """One retrieval fallback window run with its coverage report."""

    basis: str | None
    window: Mapping[str, Any]
    coverage: Mapping[str, Any]
    coverage_digest: str | None


async def run_fallback_window(
    question: Mapping[str, Any],
    port: EngineDecisionPort | None,
    *,
    run_ref: str,
    already_ran: bool = False,
) -> FallbackWindow | None:
    """Run the retrieval fallback window exactly once.

    The question goes to one ``temporal_basis`` decision; the returned
    fallback window runs through one ``UnifiedQuery`` read, never
    widened locally, and the coverage is reported. A second run refuses
    ``fallback_window_repeated``; engine codes pass through. The basis,
    the window and the coverage enter the trace.
    """
    if already_ran:
        raise ConsumerRefused("fallback_window_repeated")
    if port is None:
        raise EngineDecisionRefused("engine_decision_unavailable")
    answer = await port.decide(
        "temporal_basis", {"question": dict(question)}, run_ref=run_ref
    )
    if answer.refusal_code is not None:
        raise ConsumerRefused(answer.refusal_code)
    window = (
        answer.payload.get("window")
        if answer.outcome in ("solved", "committed")
        else None
    )
    if not isinstance(window, Mapping):
        return None
    read_answer = await port.read(
        "UnifiedQuery",
        {"question": dict(question), "window": dict(window)},
        run_ref=run_ref,
    )
    if read_answer.refusal_code is not None:
        raise ConsumerRefused(read_answer.refusal_code)
    return FallbackWindow(
        basis=answer.record_digest,
        window=dict(window),
        coverage=dict(read_answer.payload),
        coverage_digest=read_answer.record_digest,
    )


def apply_input_projection(
    task: Mapping[str, Any], projection: Mapping[str, Any]
) -> Mapping[str, Any]:
    """Verify the received input projection and return the worker payload.

    The parallel dispatch sends each worker only the projected payload
    it received from the engine. The projected digest is verified
    before the call; a projection outside its closed shape refuses
    ``input_projection_invalid`` and a digest that does not match the
    payload refuses ``input_projection_digest_mismatch``.
    """
    task_id = task.get("task_id")
    if type(task_id) is not str or not task_id:
        raise ValueError("the projected task needs an id")
    if not isinstance(projection, Mapping):
        raise ConsumerRefused("input_projection_invalid")
    payload = projection.get("payload")
    if not isinstance(payload, Mapping):
        raise ConsumerRefused("input_projection_invalid")
    digest = projection.get("digest")
    if type(digest) is not str or digest != _canonical_digest(payload):
        raise ConsumerRefused("input_projection_digest_mismatch")
    return payload


__all__ = [
    "BranchOutcome",
    "BranchSubmission",
    "BoundaryRequest",
    "ChosenUnit",
    "CompiledPartition",
    "ContextPackage",
    "ContextReplacement",
    "CouncilDecision",
    "CouncilPosition",
    "CurriculumDecision",
    "FallbackWindow",
    "HorizonCheckpoint",
    "InsightSubmission",
    "PlanScaffold",
    "SkillOrder",
    "StepVerdict",
    "TaskSkillOrder",
    "apply_input_projection",
    "apply_selected_insights",
    "boundary_params",
    "compile_context_package",
    "compile_partition",
    "consider_context_replacement",
    "order_skills",
    "record_injected_insights",
    "run_fallback_window",
    "run_horizons",
    "submit_branch_outcomes",
    "submit_council",
    "submit_curriculum",
    "submit_insight",
    "verify_step",
]


CODE_STEP_RETURN_TOO_LARGE = "CODE_STEP_RETURN_TOO_LARGE"
CODE_STEP_RETURN_MAX_BYTES = 16384
CODE_STEP_TIMEOUT_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class CodeStepResult:
    """The outcome of one code step: only ``value`` reaches the model context."""

    status: str
    value: str = ""
    code: str | None = None


async def run_code_step(
    step: Mapping[str, Any],
    workspace: Any,
    *,
    return_max_bytes: int = CODE_STEP_RETURN_MAX_BYTES,
) -> CodeStepResult:
    """Run a received compiled script in the sandbox; make no model call.

    The script calls served operations by operation id from inside the
    sandbox. Its standard output is the return value. Intermediate results
    stay in the sandbox; a return above ``return_max_bytes`` fails the step.
    """
    if step.get("mode") != "code":
        raise ValueError("run_code_step needs a step with mode code")
    exit_code, out, _err = await workspace.exec_shell(
        str(step["script"]), {}, CODE_STEP_TIMEOUT_SECONDS
    )
    if exit_code != 0:
        return CodeStepResult("failed", code=f"EXIT_{exit_code}")
    if len(out.encode()) > return_max_bytes:
        return CodeStepResult("failed", code=CODE_STEP_RETURN_TOO_LARGE)
    return CodeStepResult("completed", value=out)
