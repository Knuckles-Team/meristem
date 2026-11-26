"""The propose–probe–compose–persist–reflect harness evolution cycle."""

from __future__ import annotations

import dataclasses

import ports_support as support
import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from support.recorded_engine import RecordedEnginePort, params_digest

from meristem.graph import RunBudget, RunPlan, RunRequest, RunStatus
from meristem.graph.harness_cycle import (
    ABSTAIN_REASONS,
    ContrastAnswer,
    CycleOutcome,
    CycleRefusal,
    HarnessCandidate,
    ProbeTrace,
    collect_trace_refs,
    compose,
    conditions_digest,
    persist,
    probe_candidates,
    propose,
    reflect,
    run_cycle_tick,
)
from meristem.harness.run_lifecycle import ConsumerRefused


@pytest.fixture(autouse=True)
def _fixture_probe_executor(monkeypatch):
    """Exercise downstream mechanics with a synthetic, unauthenticated probe."""
    from meristem.graph.runner import execute_graph

    async def fake_probe(*, graph, config, task, budget_ref, **kwargs):
        return await execute_graph(graph, config, task, **kwargs, ports=support.PORTS)

    support.register_harness_probe_provider(fake_probe)


def _candidate() -> HarnessCandidate:
    return HarnessCandidate(
        candidate_id="c-1",
        task_ref="task-1",
        component_kind="system_prompt",
        component_version_ref="eg:component/system_prompt/3",
        seed="seed-1",
        model_ref="model-1",
        budget_ref="budget-1",
    )


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R001.1")
def test_model_round_trips() -> None:
    candidate = _candidate()
    assert HarnessCandidate(**dataclasses.asdict(candidate)) == candidate

    outcome = CycleOutcome(
        task_ref="task-1",
        k=2,
        adopted_version_ref=None,
        abstain_reason="budget_below_contrast",
        contrast_ref=None,
        lesson_ref=None,
    )
    assert CycleOutcome(**dataclasses.asdict(outcome)) == outcome

    adopted = CycleOutcome(
        task_ref="task-1",
        k=2,
        adopted_version_ref="eg:component/system_prompt/3",
        abstain_reason=None,
        contrast_ref="eg:contrast/1",
        lesson_ref="eg:lesson/1",
    )
    assert CycleOutcome(**dataclasses.asdict(adopted)) == adopted

    for code in ABSTAIN_REASONS:
        abstained = CycleOutcome(
            task_ref="task-1",
            k=1,
            adopted_version_ref=None,
            abstain_reason=code,
            contrast_ref=None,
            lesson_ref=None,
        )
        assert CycleOutcome(**dataclasses.asdict(abstained)) == abstained

    with pytest.raises(ValueError, match="open refusal code"):
        CycleOutcome(
            task_ref="task-1",
            k=2,
            adopted_version_ref=None,
            abstain_reason="because",
            contrast_ref=None,
            lesson_ref=None,
        )
    with pytest.raises(ValueError, match="adopts nothing"):
        CycleOutcome(
            task_ref="task-1",
            k=2,
            adopted_version_ref="eg:component/system_prompt/3",
            abstain_reason="compose_abstention",
            contrast_ref=None,
            lesson_ref=None,
        )


def _probe_candidates() -> list[HarnessCandidate]:
    return [
        HarnessCandidate(
            candidate_id="c-0",
            task_ref="task-1",
            component_kind="system_prompt",
            component_version_ref="v-a",
            seed="seed-1",
            model_ref="model-1",
            budget_ref="budget-1",
        ),
        HarnessCandidate(
            candidate_id="c-1",
            task_ref="task-1",
            component_kind="guardrails",
            component_version_ref="v-b",
            seed="seed-1",
            model_ref="model-1",
            budget_ref="budget-1",
        ),
    ]


@pytest.mark.parametrize("field", ["seed", "model_ref", "budget_ref"])
async def test_probe_rejects_inconsistent_candidates_before_any_run(
    field: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def unexpected_run(*args, **kwargs):
        raise AssertionError("a mismatched candidate must not start a probe")

    support.register_harness_probe_provider(unexpected_run)
    candidates = _probe_candidates()
    candidates[1] = dataclasses.replace(candidates[1], **{field: "different"})

    with pytest.raises(CycleRefusal, match="probe_trace_missing"):
        await probe_candidates(
            graph=object(),
            config=None,
            task="classify",
            seed="seed-1",
            model_ref="model-1",
            budget_ref="budget-1",
            run_id="run-1",
            current_version_ref="v-0",
            candidates=candidates,
            ports=support.PORTS,
        )


async def test_probe_shares_conditions_and_records_trace() -> None:
    def model(messages, info):
        return ModelResponse(parts=[TextPart("done")])

    support.register_run_decision_provider(
        lambda request: RunPlan(model=FunctionModel(model), steps=(request.task,))
    )

    traces = await probe_candidates(
        graph=object(),
        config=None,
        task="classify",
        seed="seed-1",
        model_ref="model-1",
        budget_ref="budget-1",
        run_id="run-1",
        current_version_ref="",
        candidates=_probe_candidates(),
        ports=support.PORTS,
    )

    assert [trace.candidate_id for trace in traces] == ["c-0", "c-1"]
    assert [trace.trace_id for trace in traces] == [
        "run-1:probe:c-0",
        "run-1:probe:c-1",
    ]
    assert {trace.conditions_digest for trace in traces} == {
        conditions_digest(
            task="classify", seed="seed-1", model_ref="model-1", budget_ref="budget-1"
        )
    }
    for trace in traces:
        assert trace.status is RunStatus.COMPLETED
        assert trace.error is None
        assert trace.steps == 1


async def test_probe_records_current_harness_baseline() -> None:
    def model(messages, info):
        return ModelResponse(parts=[TextPart("done")])

    support.register_run_decision_provider(
        lambda request: RunPlan(model=FunctionModel(model), steps=(request.task,))
    )

    traces = await probe_candidates(
        graph=object(),
        config=None,
        task="classify",
        seed="seed-1",
        model_ref="model-1",
        budget_ref="budget-1",
        run_id="run-1",
        current_version_ref="v-0",
        candidates=_probe_candidates(),
        ports=support.PORTS,
    )

    assert [trace.candidate_id for trace in traces] == [
        "current",
        "c-0",
        "c-1",
    ]
    assert traces[0].trace_id == "run-1:probe:current"
    assert {trace.conditions_digest for trace in traces} == {
        conditions_digest(
            task="classify", seed="seed-1", model_ref="model-1", budget_ref="budget-1"
        )
    }


def _trace(
    candidate_id: str, *, status: str = "completed", tool_calls: int = 0
) -> ProbeTrace:
    return ProbeTrace(
        candidate_id=candidate_id,
        trace_id=f"run-1:probe:{candidate_id}",
        conditions_digest="digest-1",
        status=status,
        steps=1,
        usage={"requests": 1, "tool_calls": tool_calls, "total_tokens": 10},
        error=None if status == "completed" else "boom",
    )


def _contrast_params(
    *,
    task_ref,
    task_class,
    conditions_digest_value,
    candidates,
    baseline,
    traces,
) -> dict:
    """The params the compose step sends under the contrast kind."""
    refs = collect_trace_refs(baseline=baseline, candidates=candidates, traces=traces)
    return {
        "task_ref": task_ref,
        "task_class": task_class,
        "conditions_digest": conditions_digest_value,
        "candidate_version_refs": list(refs.candidate_version_refs),
        "baseline_trace_id": refs.baseline_trace_id,
        "probe_trace_ids": list(refs.probe_trace_ids),
    }


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R001.12")
@pytest.mark.parametrize("has_baseline", [False, True])
async def test_compose_refuses_unmeasured_contrast_before_decide(has_baseline) -> None:
    candidates = _probe_candidates()
    traces = {
        candidate.candidate_id: _trace(candidate.candidate_id, tool_calls=100)
        for candidate in candidates
    }
    baseline = _trace("current") if has_baseline else None
    kwargs = {
        "task_ref": "task-1",
        "task_class": "classification",
        "conditions_digest_value": "digest-1",
        "candidates": candidates,
        "baseline": baseline,
        "traces": traces,
        "ports": support.PORTS,
        "run_ref": "run-1",
    }

    # An absent engine port refuses before any Decide call.
    support.register_engine_decision_port(None)
    with pytest.raises(CycleRefusal, match="measurement_unavailable"):
        await compose(**kwargs)

    # An insufficient_evidence answer refuses the same way, after one Decide.
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "harness_contrast",
                    "params": _contrast_params(
                        task_ref="task-1",
                        task_class="classification",
                        conditions_digest_value="digest-1",
                        candidates=candidates,
                        baseline=baseline,
                        traces=traces,
                    ),
                    "answer": {
                        "outcome": "abstained",
                        "refusal_code": "insufficient_evidence",
                    },
                }
            ]
        }
    )
    support.register_engine_decision_port(port)
    with pytest.raises(CycleRefusal, match="measurement_unavailable"):
        await compose(**kwargs)
    assert [call[0] for call in port.calls] == ["harness_contrast"]


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R001.4")
async def test_compose_delegates_to_engine() -> None:
    candidates = _probe_candidates()
    baseline = _trace("current")
    traces = {
        candidate.candidate_id: _trace(candidate.candidate_id)
        for candidate in candidates
    }
    params = {
        "task_ref": "task-1",
        "task_class": "classification",
        "conditions_digest": "digest-1",
        "candidate_version_refs": [
            candidates[0].component_version_ref,
            candidates[1].component_version_ref,
        ],
        "baseline_trace_id": baseline.trace_id,
        "probe_trace_ids": [
            traces[candidates[0].candidate_id].trace_id,
            traces[candidates[1].candidate_id].trace_id,
        ],
    }
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "harness_contrast",
                    "params": params,
                    "answer": {
                        "outcome": "committed",
                        "record_digest": "contrast-record-1",
                        "payload": {
                            "adopted_version_ref": candidates[1].component_version_ref,
                            "adopted_component_kind": "guardrails",
                            "adopted_content": "new guardrails",
                        },
                    },
                }
            ]
        }
    )
    support.register_engine_decision_port(port)

    answer, record_digest = await compose(
        task_ref="task-1",
        task_class="classification",
        conditions_digest_value="digest-1",
        candidates=candidates,
        baseline=baseline,
        traces=traces,
        ports=support.PORTS,
        run_ref="run-1",
    )

    # One Decide under the contrast kind; the request holds the trace
    # references in candidate order and no delta — the cycle ranks nothing.
    assert port.calls == [("harness_contrast", params_digest(params))]
    for field in (
        "retry_delta",
        "tool_selection_delta",
        "eval_score_delta",
        "cost_delta",
        "contrasts",
    ):
        assert field not in params
    # The engine's answer stands, even for a later candidate; its record
    # digest is the contrast reference.
    assert answer.adopted_version_ref == candidates[1].component_version_ref
    assert answer.abstain_reason is None
    assert answer.adopted_component_kind == "guardrails"
    assert answer.adopted_content == "new guardrails"
    assert record_digest == "contrast-record-1"


async def test_compose_abstains_when_engine_abstains() -> None:
    candidates = _probe_candidates()
    baseline = _trace("current")
    traces = {
        candidate.candidate_id: _trace(candidate.candidate_id)
        for candidate in candidates
    }
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "harness_contrast",
                    "params": _contrast_params(
                        task_ref="task-1",
                        task_class="classification",
                        conditions_digest_value="digest-1",
                        candidates=candidates,
                        baseline=baseline,
                        traces=traces,
                    ),
                    "answer": {"outcome": "abstained"},
                }
            ]
        }
    )
    support.register_engine_decision_port(port)

    with pytest.raises(CycleRefusal, match="compose_abstention"):
        await compose(
            task_ref="task-1",
            task_class="classification",
            conditions_digest_value="digest-1",
            candidates=candidates,
            baseline=baseline,
            traces=traces,
            ports=support.PORTS,
            run_ref="run-1",
        )
    assert [call[0] for call in port.calls] == ["harness_contrast"]


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R001.14")
async def test_compose_sends_trace_refs_and_maps_answers() -> None:
    candidates = _probe_candidates()
    baseline = _trace("current")
    traces = {
        candidate.candidate_id: _trace(candidate.candidate_id)
        for candidate in candidates
    }
    params = _contrast_params(
        task_ref="task-1",
        task_class="classification",
        conditions_digest_value="digest-1",
        candidates=candidates,
        baseline=baseline,
        traces=traces,
    )
    kwargs = {
        "task_ref": "task-1",
        "task_class": "classification",
        "conditions_digest_value": "digest-1",
        "candidates": candidates,
        "baseline": baseline,
        "traces": traces,
        "ports": support.PORTS,
        "run_ref": "run-1",
    }

    def recorded(answer):
        support.register_engine_decision_port(
            RecordedEnginePort(
                {
                    "answers": [
                        {
                            "method": "harness_contrast",
                            "params": params,
                            "answer": answer,
                        }
                    ]
                }
            )
        )

    # The adoption answer: the request holds no delta and the answer's
    # record digest is returned as the contrast reference.
    recorded(
        {
            "outcome": "committed",
            "record_digest": "contrast-record-9",
            "payload": {
                "adopted_version_ref": "v-b",
                "adopted_component_kind": "guardrails",
                "adopted_content": "new guardrails",
            },
        }
    )
    port = support.PORTS.engine_decision_port()
    answer, record_digest = await compose(**kwargs)
    assert port.calls == [("harness_contrast", params_digest(params))]
    assert answer.adopted_version_ref == "v-b"
    assert record_digest == "contrast-record-9"

    # The abstained answer maps to compose_abstention.
    recorded({"outcome": "abstained"})
    with pytest.raises(CycleRefusal, match="compose_abstention"):
        await compose(**kwargs)

    # insufficient_evidence maps to measurement_unavailable.
    recorded({"outcome": "abstained", "refusal_code": "insufficient_evidence"})
    with pytest.raises(CycleRefusal, match="measurement_unavailable"):
        await compose(**kwargs)

    # Engine codes pass through unchanged.
    for code in (
        "HARNESS_CONTRAST_TRACE_MISMATCH",
        "HARNESS_COMPONENT_VERSION_UNKNOWN",
    ):
        recorded({"outcome": "abstained", "refusal_code": code})
        with pytest.raises(ConsumerRefused, match=code):
            await compose(**kwargs)

    # An absent port refuses before any Decide call.
    support.register_engine_decision_port(None)
    with pytest.raises(CycleRefusal, match="measurement_unavailable"):
        await compose(**kwargs)


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R001.12")
@pytest.mark.parametrize("port_bound", [False, True])
async def test_persist_refuses_unverified_contrast_before_writes_or_switch(
    tmp_path, port_bound
) -> None:
    component = tmp_path / "harness" / "system_prompt.md"
    component.parent.mkdir(parents=True)
    component.write_text("current prompt", encoding="utf-8")
    writes: list[object] = []
    if port_bound:
        support.register_engine_decision_port(
            RecordedEnginePort(
                {
                    "answers": [
                        {
                            "method": "log",
                            "params": {"op": "HarnessAdoption"},
                            "answer": {
                                "outcome": "abstained",
                                "refusal_code": "insufficient_evidence",
                            },
                        }
                    ]
                }
            )
        )
    else:
        support.register_engine_decision_port(None)
    with pytest.raises(CycleRefusal, match="measurement_unavailable"):
        await persist(
            task_ref="task-1",
            task_class="classification",
            decision_record_digest="contrast-record-1",
            conditions_digest_value="digest-1",
            adopted=_probe_candidates()[0],
            answer=ContrastAnswer(
                adopted_version_ref="v-a",
                abstain_reason=None,
                adopted_component_kind="system_prompt",
                adopted_content="changed",
            ),
            evidence_run_ids=(),
            ports=support.PORTS,
            run_ref="task-1",
            write_envelope=writes.append,
            root=tmp_path,
        )
    assert writes == []
    assert component.read_text(encoding="utf-8") == "current prompt"


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R001.5")
async def test_persist_logs_one_adoption_then_switches(tmp_path, monkeypatch) -> None:
    import meristem.graph.harness_cycle as cycle

    adopted = HarnessCandidate(
        candidate_id="task-1:candidate:0",
        task_ref="task-1",
        component_kind="system_prompt",
        component_version_ref="eg:component/system_prompt/3",
        seed="seed-1",
        model_ref="model-1",
        budget_ref="budget-1",
    )
    answer = ContrastAnswer(
        adopted_version_ref="eg:component/system_prompt/3",
        abstain_reason=None,
        adopted_component_kind="system_prompt",
        adopted_content="adopted prompt",
    )
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "log",
                    "params": {"op": "HarnessAdoption"},
                    "answer": {
                        "outcome": "committed",
                        "record_digest": "adoption-record-1",
                        "payload": {
                            "adoption_ref": "eg:adoption/1",
                            "contrast_ref": "eg:contrast/1",
                            "lesson_ref": "eg:lesson/1",
                        },
                    },
                }
            ]
        }
    )
    support.register_engine_decision_port(port)
    real_apply = cycle.apply_edits
    apply_calls: list[int] = []

    def spy_apply(edits, root=".", *, dry_run=False):
        apply_calls.append(len(port.calls))
        return real_apply(edits, root=root, dry_run=dry_run)

    monkeypatch.setattr(cycle, "apply_edits", spy_apply)
    writes: list[dict] = []

    version_ref, contrast_ref, lesson_ref = await persist(
        task_ref="task-1",
        task_class="classification",
        decision_record_digest="contrast-record-1",
        conditions_digest_value="digest-1",
        adopted=adopted,
        answer=answer,
        evidence_run_ids=("task-1:probe:current", "task-1:probe:task-1:candidate:0"),
        ports=support.PORTS,
        run_ref="task-1",
        write_envelope=writes.append,
        root=tmp_path,
    )

    assert (version_ref, contrast_ref, lesson_ref) == (
        "eg:adoption/1",
        "eg:contrast/1",
        "eg:lesson/1",
    )
    # One log call, and one apply_edits call after it — the switch
    # happens only after the commit, never before.
    assert [call[0] for call in port.calls] == ["log"]
    assert apply_calls == [1]
    assert (tmp_path / ".harness" / "system_prompt.md").read_text(
        encoding="utf-8"
    ) == "adopted prompt"
    # The adoption envelope carries the harness-authored lesson; the
    # progress-facts record follows it through the same writer.
    assert len(writes) == 2
    assert writes[0]["adopted_version_ref"] == adopted.component_version_ref
    assert writes[0]["decision_record_digest"] == "contrast-record-1"
    assert writes[1]["kind"] == "harness_evolution_progress"
    assert writes[0]["lesson"]["changed_component_kind"] == "system_prompt"
    assert writes[0]["lesson"]["what_changed"] == adopted.component_version_ref
    assert writes[0]["lesson"]["evidence_run_ids"] == [
        "task-1:probe:current",
        "task-1:probe:task-1:candidate:0",
    ]

    # Passed-through engine codes: no edit and no switch on a refusal.
    for code in (
        "LessonWithoutEvidence",
        "ContrastConditionsMismatch",
        "AdoptedHarnessNoDecision",
    ):
        support.register_engine_decision_port(
            RecordedEnginePort(
                {
                    "answers": [
                        {
                            "method": "log",
                            "params": {"op": "HarnessAdoption"},
                            "answer": {
                                "outcome": "abstained",
                                "refusal_code": code,
                            },
                        }
                    ]
                }
            )
        )
        with pytest.raises(ConsumerRefused, match=code):
            await persist(
                task_ref="task-1",
                task_class="classification",
                decision_record_digest="contrast-record-1",
                conditions_digest_value="digest-1",
                adopted=adopted,
                answer=answer,
                evidence_run_ids=(),
                ports=support.PORTS,
                run_ref="task-1",
                write_envelope=writes.append,
                root=tmp_path,
            )
    assert apply_calls == [1]
    # The refusals wrote nothing; only the committed tick's two envelopes
    # stand.
    assert len(writes) == 2


def _library() -> list[dict[str, str]]:
    return [
        {"component_kind": "system_prompt", "component_version_ref": "v-a"},
        {"component_kind": "guardrails", "component_version_ref": "v-b"},
    ]


def _tick_request() -> RunRequest:
    return support.request(
        task="classify",
        run_id="task-1",
        metadata={
            "harness_seed": "seed-1",
            "harness_current_version_ref": "v-0",
        },
    )


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R001.7")
@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R001.12")
@pytest.mark.parametrize("has_baseline", [False, True])
async def test_tick_refuses_without_owner_measurements_before_decide_or_persist(
    tmp_path, events, has_baseline
) -> None:
    def model(messages, info):
        return ModelResponse(parts=[TextPart("done")])

    support.register_run_decision_provider(
        lambda request: RunPlan(
            model=FunctionModel(model),
            steps=(request.task,),
            budget=RunBudget(candidate_slots=2),
        )
    )
    metadata = {"harness_seed": "seed-1"}
    if has_baseline:
        metadata["harness_current_version_ref"] = "v-0"
    request = support.request(task="classify", run_id="task-1", metadata=metadata)
    support.register_engine_decision_port(None)
    writes: list[object] = []
    component = tmp_path / "harness" / "system_prompt.md"
    component.parent.mkdir(parents=True)
    component.write_text("current prompt", encoding="utf-8")

    outcome = await run_cycle_tick(
        request,
        RunPlan(model=FunctionModel(model), budget=RunBudget(candidate_slots=2)),
        task_class="classification",
        graph=object(),
        library_query=lambda _class: _library(),
        write_envelope=writes.append,
        root=tmp_path,
    )
    assert outcome.abstain_reason == "measurement_unavailable"
    assert outcome.adopted_version_ref is None
    assert outcome.contrast_ref is None and outcome.lesson_ref is None
    assert writes == []
    assert component.read_text(encoding="utf-8") == "current prompt"
    cycle_events = [
        event for event in events if event.get("type") == "harness_cycle_outcome"
    ]
    assert len(cycle_events) == 1
    assert cycle_events[0]["abstain_reason"] == "measurement_unavailable"
    assert cycle_events[0]["evidence_run_ids"] == []


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R001.6")
def test_reflect_reports_adoption_or_abstention(events) -> None:
    adopted = CycleOutcome(
        task_ref="task-1",
        k=2,
        adopted_version_ref="v-a",
        abstain_reason=None,
        contrast_ref="ref-contrast",
        lesson_ref="ref-lesson",
    )
    reflect(adopted, evidence_run_ids=("baseline", "candidate"), ports=support.PORTS)
    assert events[-1]["lesson_ref"] == "ref-lesson"
    assert events[-1]["evidence_run_ids"] == ["baseline", "candidate"]
    abstained = CycleOutcome(
        task_ref="task-1",
        k=2,
        adopted_version_ref=None,
        abstain_reason="measurement_unavailable",
        contrast_ref=None,
        lesson_ref=None,
    )
    reflect(abstained, evidence_run_ids=("baseline", "candidate"), ports=support.PORTS)
    assert events[-1]["lesson_ref"] is None
    assert events[-1]["evidence_run_ids"] == []


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R001.7")
async def test_refusals_keep_harness(tmp_path) -> None:
    import meristem.graph.harness_cycle as cycle

    def model(messages, info):
        return ModelResponse(parts=[TextPart("done")])

    def plan(candidate_slots):
        return RunPlan(
            model=FunctionModel(model),
            steps=("task",),
            budget=RunBudget(candidate_slots=candidate_slots),
        )

    def registered_provider(request):
        return RunPlan(
            model=FunctionModel(model),
            steps=(request.task,),
            budget=RunBudget(candidate_slots=2),
        )

    support.register_run_decision_provider(registered_provider)

    component = tmp_path / "harness" / "system_prompt.md"
    component.parent.mkdir(parents=True)
    component.write_text("current prompt", encoding="utf-8")
    written: list[str] = []
    queried: list[str] = []

    def library(task_class):
        queried.append(task_class)
        return _library()

    def write_envelope(envelope):
        written.append(str(envelope["kind"]))
        return f"ref-{envelope['kind']}"

    # budget_below_contrast: an allocation under the candidate bound.
    for slots in (1, 0, None):
        outcome = await run_cycle_tick(
            _tick_request(),
            plan(slots),
            task_class="classification",
            graph=object(),
            library_query=library,
            write_envelope=write_envelope,
            root=tmp_path,
        )
        assert outcome.abstain_reason == "budget_below_contrast"
        assert outcome.k == (slots or 0)
        assert outcome.adopted_version_ref is None
        assert outcome.contrast_ref is None and outcome.lesson_ref is None
    assert queried == [] and written == []

    # no_candidates: an empty library answer.
    outcome = await run_cycle_tick(
        _tick_request(),
        plan(2),
        task_class="classification",
        graph=object(),
        library_query=lambda _class: [],
        write_envelope=write_envelope,
        root=tmp_path,
    )
    assert outcome.abstain_reason == "no_candidates"
    assert outcome.k == 2
    assert written == []

    # probe_trace_missing: a lost probe trace.
    async def lost_probe(*args, **kwargs):
        raise RuntimeError("trace lost")

    fixture_probe = support.PORTS.harness_probe_provider()
    support.register_harness_probe_provider(lost_probe)
    outcome = await run_cycle_tick(
        _tick_request(),
        plan(2),
        task_class="classification",
        graph=object(),
        library_query=library,
        write_envelope=write_envelope,
        root=tmp_path,
    )
    assert outcome.abstain_reason == "probe_trace_missing"
    assert written == []

    # compose_abstention: the engine abstains, and no adoption persists.
    support.register_harness_probe_provider(fixture_probe)
    plan2 = plan(2)
    tick_digest = conditions_digest(
        task="classify",
        seed="seed-1",
        model_ref=cycle._model_ref(plan2.model),
        budget_ref=cycle._budget_ref(plan2.budget),
    )
    # propose names its candidates <task_ref>:candidate:<index>.
    tick_params = {
        "task_ref": "task-1",
        "task_class": "classification",
        "conditions_digest": tick_digest,
        "candidate_version_refs": ["v-a", "v-b"],
        "baseline_trace_id": "task-1:probe:current",
        "probe_trace_ids": [
            "task-1:probe:task-1:candidate:0",
            "task-1:probe:task-1:candidate:1",
        ],
    }
    support.register_engine_decision_port(
        RecordedEnginePort(
            {
                "answers": [
                    {
                        "method": "harness_contrast",
                        "params": tick_params,
                        "answer": {"outcome": "abstained"},
                    }
                ]
            }
        )
    )
    outcome = await run_cycle_tick(
        _tick_request(),
        plan(2),
        task_class="classification",
        graph=object(),
        library_query=library,
        write_envelope=write_envelope,
        root=tmp_path,
    )
    assert outcome.abstain_reason == "compose_abstention"
    assert outcome.adopted_version_ref is None
    assert written == []

    # Every refusal kept the current harness.
    assert component.read_text(encoding="utf-8") == "current prompt"


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R001.15")
async def test_slot_bounds_come_from_the_plan_budget(tmp_path) -> None:
    def model(messages, info):
        return ModelResponse(parts=[TextPart("done")])

    support.register_run_decision_provider(
        lambda request: RunPlan(
            model=FunctionModel(model),
            steps=(request.task,),
            budget=RunBudget(candidate_slots=2),
        )
    )
    component = tmp_path / "harness" / "system_prompt.md"
    component.parent.mkdir(parents=True)
    component.write_text("current prompt", encoding="utf-8")

    def plan(slots, min_slots):
        return RunPlan(
            model=FunctionModel(model),
            steps=("task",),
            budget=RunBudget(candidate_slots=slots, candidate_slots_min=min_slots),
        )

    def tick(plan_obj, library=None):
        return run_cycle_tick(
            _tick_request(),
            plan_obj,
            task_class="classification",
            graph=object(),
            library_query=library if library is not None else lambda _class: _library(),
            write_envelope=lambda envelope: "ref",
            root=tmp_path,
        )

    # The received bound from the stage allocation governs: it abstains
    # where the fallback would have admitted the two slots.
    outcome = await tick(plan(2, 3))
    assert outcome.abstain_reason == "budget_below_contrast"
    assert outcome.k == 2
    assert outcome.adopted_version_ref is None

    # The received bound also admits where the fallback would have
    # refused: one slot with a received minimum of one.
    outcome = await tick(plan(1, 1))
    assert outcome.abstain_reason != "budget_below_contrast"
    assert outcome.k == 1

    # Without a received minimum the fallback of two slots applies.
    outcome = await tick(plan(2, None))
    assert outcome.abstain_reason != "budget_below_contrast"
    assert outcome.k == 2
    outcome = await tick(plan(1, None))
    assert outcome.abstain_reason == "budget_below_contrast"
    assert outcome.k == 1
    outcome = await tick(plan(None, None))
    assert outcome.abstain_reason == "budget_below_contrast"
    assert outcome.k == 0

    # The tick keeps at most k candidates, never more than the engine
    # maximum: three library versions under two slots probe two candidates.
    probes: list[dict[str, object]] = []

    async def counting_probe(**kwargs):
        probes.append(kwargs)
        return {"run_id": kwargs["run_id"], "status": "completed", "steps": 1}

    fixture_probe = support.PORTS.harness_probe_provider()
    support.register_harness_probe_provider(counting_probe)
    three = _library() + [{"component_kind": "memory", "component_version_ref": "v-c"}]
    outcome = await tick(plan(2, None), library=lambda _class: three)
    support.register_harness_probe_provider(fixture_probe)
    assert outcome.abstain_reason != "budget_below_contrast"
    # The current-harness probe first, then the k candidates; the third
    # library version never probes because the engine maximum is k.
    assert [
        str(call["metadata"]["harness_probe"]["component_version_ref"])
        for call in probes
    ] == ["v-0", "v-a", "v-b"]
    assert component.read_text(encoding="utf-8") == "current prompt"


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R001.8")
async def test_port_fails_closed_without_provider() -> None:
    from meristem.graph.harness_cycle import drive_cycle_tick

    with pytest.raises(RuntimeError, match="arbora"):
        await drive_cycle_tick(
            support.request(task="classify"),
            RunPlan(model="m-1"),
            task_class="classification",
            graph=object(),
        )


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R001.8")
async def test_drive_delegates_to_registered_provider() -> None:
    from meristem.graph.harness_cycle import drive_cycle_tick

    outcome = CycleOutcome(
        task_ref="task-1",
        k=2,
        adopted_version_ref="v-a",
        abstain_reason=None,
        contrast_ref="ref-harness_contrast",
        lesson_ref="ref-harness_lesson",
    )
    seen: list[tuple[object, object]] = []

    async def provider(request, plan, **kwargs):
        seen.append((request, kwargs))
        return outcome

    support.register_harness_cycle_provider(provider)
    assert (
        await drive_cycle_tick(
            support.request(task="classify"),
            RunPlan(model="m-1"),
            task_class="classification",
            graph=object(),
        )
        is outcome
    )
    assert seen[0][0].task == "classify"
    assert seen[0][1]["task_class"] == "classification"

    support.register_harness_cycle_provider(lambda *args, **kwargs: "not an outcome")
    with pytest.raises(TypeError, match="CycleOutcome"):
        await drive_cycle_tick(
            support.request(task="classify"),
            RunPlan(model="m-1"),
            task_class="classification",
            graph=object(),
        )


def test_cycle_refusal_rejects_open_code() -> None:
    for code in ABSTAIN_REASONS:
        assert CycleRefusal(code).code == code
    with pytest.raises(ValueError, match="open refusal code"):
        CycleRefusal("because")


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R001.2")
@pytest.mark.parametrize(
    ("returned", "k", "expected_refs"),
    [
        (
            [
                {"component_kind": "system_prompt", "component_version_ref": "v-a"},
                {"component_kind": "guardrails", "component_version_ref": "v-b"},
            ],
            2,
            ("v-a", "v-b"),
        ),
        (
            [
                {"component_kind": "system_prompt", "component_version_ref": "v-a"},
                {"component_kind": "guardrails", "component_version_ref": "v-b"},
                {"component_kind": "memory", "component_version_ref": "v-c"},
            ],
            2,
            ("v-a", "v-b"),
        ),
    ],
)
def test_propose_queries_lesson_library(returned, k, expected_refs) -> None:
    queried: list[str] = []

    def library(task_class: str) -> list[dict[str, str]]:
        queried.append(task_class)
        return [dict(entry) for entry in returned]

    candidates = propose(
        task_ref="task-1",
        task_class="classification",
        library=library,
        seed="seed-1",
        model_ref="model-1",
        budget_ref="budget-1",
        k=k,
    )

    assert queried == ["classification"]
    assert tuple(c.component_version_ref for c in candidates) == expected_refs
    for candidate in candidates:
        assert candidate.task_ref == "task-1"
        assert candidate.seed == "seed-1"
        assert candidate.model_ref == "model-1"
        assert candidate.budget_ref == "budget-1"
        with pytest.raises(dataclasses.FrozenInstanceError):
            candidate.seed = "other"  # type: ignore[misc]

    # The cycle stores nothing: a rerun re-queries the library and stores no
    # answer of its own (no write, no cache).
    again = propose(
        task_ref="task-1",
        task_class="classification",
        library=library,
        seed="seed-1",
        model_ref="model-1",
        budget_ref="budget-1",
        k=k,
    )
    assert again == candidates
    assert queried == ["classification", "classification"]

    with pytest.raises(CycleRefusal, match="no_candidates"):
        propose(
            task_ref="task-1",
            task_class="classification",
            library=lambda _class: [],
            seed="seed-1",
            model_ref="model-1",
            budget_ref="budget-1",
            k=k,
        )


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R001.13")
async def test_missing_host_refuses_before_any_probe(monkeypatch):
    support.register_harness_probe_provider(None)
    with pytest.raises(CycleRefusal, match="probe_trace_missing"):
        await probe_candidates(
            graph=object(),
            config=None,
            task="task",
            seed="seed",
            model_ref="model",
            budget_ref="budget",
            run_id="run",
            current_version_ref="version",
            candidates=[],
            ports=support.PORTS,
        )


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R001.13")
async def test_host_gets_candidate_and_requested_conditions(monkeypatch):
    calls = []

    async def provider(**kwargs):
        calls.append(kwargs)
        return {"run_id": kwargs["run_id"], "status": "completed"}

    support.register_harness_probe_provider(provider)
    traces = await probe_candidates(
        graph=object(),
        config=None,
        task="task",
        seed="seed",
        model_ref="model",
        budget_ref="budget",
        run_id="run",
        current_version_ref="version",
        candidates=[],
        ports=support.PORTS,
    )
    assert traces[0].trace_id == "run:probe:current"
    assert calls[0]["metadata"]["harness_probe"] == {
        "candidate_id": "current",
        "component_version_ref": "version",
        "seed": "seed",
    }
    assert calls[0]["requested_model_id"] == "model"
    assert calls[0]["budget_ref"] == "budget"


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R001.13")
async def test_wrong_run_identity_is_refused(monkeypatch):
    async def provider(**kwargs):
        return {"run_id": "another-run", "status": "completed"}

    support.register_harness_probe_provider(provider)
    with pytest.raises(CycleRefusal, match="probe_trace_missing"):
        await probe_candidates(
            graph=object(),
            config=None,
            task="task",
            seed="seed",
            model_ref="model",
            budget_ref="budget",
            run_id="run",
            current_version_ref="version",
            candidates=[],
            ports=support.PORTS,
        )


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R001.3")
async def test_probe_conditions_materialized_and_matched() -> None:
    """A host executor drives ``execute_graph`` per probe and echoes its conditions."""
    from meristem.graph.runner import execute_graph

    seen: list[dict] = []

    def model(messages, info):
        return ModelResponse(parts=[TextPart("done")])

    support.register_run_decision_provider(
        lambda request: RunPlan(model=FunctionModel(model), steps=(request.task,))
    )
    wrong_digest = False

    async def executor(*, graph, config, task, run_id, budget_ref, metadata, **kw):
        # The conditions this host actually materialized for the probe.
        actual = conditions_digest(
            task=task,
            seed=metadata["harness_probe"]["seed"],
            model_ref=kw["requested_model_id"],
            budget_ref=budget_ref,
        )
        seen.append({"run_id": run_id, "digest": actual})
        result = await execute_graph(
            graph,
            config,
            task,
            run_id=run_id,
            mode=kw["mode"],
            requested_model_id=kw["requested_model_id"],
            agent_name=kw["agent_name"],
            ports=support.PORTS,
        )
        return {
            **result,
            "conditions_digest": "0" * 64 if wrong_digest else actual,
        }

    support.register_harness_probe_provider(executor)
    kwargs = dict(
        graph=object(),
        config=None,
        task="classify",
        seed="seed-1",
        model_ref="model-1",
        budget_ref="budget-1",
        run_id="run-1",
        current_version_ref="v-0",
        candidates=_probe_candidates(),
        ports=support.PORTS,
    )
    traces = await probe_candidates(**kwargs)

    requested = conditions_digest(
        task="classify", seed="seed-1", model_ref="model-1", budget_ref="budget-1"
    )
    assert [t.candidate_id for t in traces] == ["current", "c-0", "c-1"]
    assert {s["digest"] for s in seen} == {requested}
    assert {t.conditions_digest for t in traces} == {requested}
    assert [t.trace_id for t in traces] == [s["run_id"] for s in seen]

    wrong_digest = True
    with pytest.raises(CycleRefusal, match="probe_trace_missing"):
        await probe_candidates(**kwargs)
