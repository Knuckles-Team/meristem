"""Served harness-cycle tests over the bound engine decision port."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import ports_support as support
import pytest
from support.recorded_engine import UnrecordedEngineCall, params_digest

from meristem import host_ports as hp
from meristem.graph.harness_cycle import ContrastAnswer, HarnessCandidate, persist
from meristem.harness.run_lifecycle import ConsumerRefused


class LoopbackEngine:
    """A loopback engine decision port.

    It stores what the harness commits through ``log`` under the run's
    principal and serves the stored records back through graph reads;
    ``decide`` replays one recorded fixture per call.
    """

    def __init__(
        self,
        task_class: str = "classification",
        decide_cases: list[dict[str, Any]] | None = None,
    ) -> None:
        self.task_class = task_class
        self.decide_cases = list(decide_cases or ())
        self.commits: list[dict[str, Any]] = []
        self.log_payload: dict[str, Any] = {}
        self.log_refusal_code: str | None = None
        self.store: dict[str, list[dict[str, Any]]] = {}
        #: The lessons the engine holds, per task class.
        self.lessons: dict[str, dict[str, Any]] = {}

    async def decide(
        self, kind: str, params: Mapping[str, Any], *, run_ref: str
    ) -> hp.EngineAnswer:
        """Replay the next recorded decision answer for kind and params."""
        for case in self.decide_cases:
            if case["method"] != kind:
                continue
            recorded = params_digest(case.get("params") or {})
            if recorded == params_digest(params):
                record = dict(case["answer"])
                return hp.EngineAnswer(
                    outcome=record["outcome"],
                    refusal_code=record.get("refusal_code"),
                    record_digest=record.get("record_digest"),
                    payload=dict(record.get("payload", ())),
                )
        raise UnrecordedEngineCall(kind, params_digest(params))

    async def assemble(
        self, request: Mapping[str, Any], *, run_ref: str
    ) -> hp.EngineAnswer:
        raise AssertionError("unexpected assembly call")

    async def log(self, op: str, *, run_ref: str) -> hp.EngineAnswer:
        """One decision-log commit: store the record, then answer."""
        if self.log_refusal_code is not None:
            return hp.EngineAnswer(
                outcome="abstained", refusal_code=self.log_refusal_code
            )
        record = dict(self.log_payload)
        record["op"] = op
        record["run_ref"] = run_ref
        self.commits.append(record)
        self.store.setdefault(op, []).append(record)
        return hp.EngineAnswer(
            outcome="committed",
            record_digest=f"loopback-record-{len(self.commits)}",
            payload=dict(self.log_payload),
        )

    async def put_blob(self, data: bytes, *, run_ref: str) -> hp.EngineAnswer:
        raise AssertionError("unexpected blob put")

    async def read(
        self, method: str, params: Mapping[str, Any], *, run_ref: str
    ) -> hp.EngineAnswer:
        """One graph read over the stored records."""
        if method == "GetNodesByLabel":
            return hp.EngineAnswer(
                outcome="solved",
                payload={"nodes": list(self.store.get(str(params.get("label")), ()))},
            )
        raise AssertionError(f"unexpected read: {method}")

    async def write(
        self, method: str, params: Mapping[str, Any], *, run_ref: str
    ) -> hp.EngineAnswer:
        raise AssertionError(f"unexpected write: {method}")


@pytest.fixture(autouse=True)
def _fixture_probe_executor():
    """The host executor runs each probe through execute_graph."""
    from meristem.graph.runner import execute_graph

    async def host_probe(*, graph, config, task, budget_ref, **kwargs):
        return await execute_graph(graph, config, task, **kwargs, ports=support.PORTS)

    support.register_harness_probe_provider(host_probe)


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R001.9")
async def test_cycle_double_shows_lesson_to_next_propose(tmp_path) -> None:
    """The served journey: one tick with two candidates and k = 2.

    The registered host provider drives the tick through
    drive_cycle_tick; the host executor runs each probe through
    execute_graph; the loopback engine replays one recorded answer per
    call and stores the lesson the tick persists for the task class.
    The test reads the engine through the port and finds the adopted
    harness, the contrast and the lesson; the next propose query
    returns the stored lesson.
    """
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import FunctionModel

    import meristem.graph.harness_cycle as cycle
    from meristem.graph import RunBudget, RunPlan
    from meristem.graph.harness_cycle import (
        conditions_digest,
        drive_cycle_tick,
        propose,
        run_cycle_tick,
    )
    from meristem.graph.runner import execute_graph

    def model(messages, info):
        return ModelResponse(parts=[TextPart("done")])

    request = support.request(
        task="classify",
        run_id="task-1",
        metadata={"harness_seed": "seed-1", "harness_current_version_ref": "v-0"},
    )
    plan = RunPlan(
        model=FunctionModel(model),
        steps=("classify",),
        budget=RunBudget(candidate_slots=2),
    )
    # One recorded engine answer per call: the contrast adoption, sent
    # with the trace references the engine measures from.
    tick_digest = conditions_digest(
        task="classify",
        seed="seed-1",
        model_ref=cycle._model_ref(plan.model),
        budget_ref=cycle._budget_ref(plan.budget),
    )
    contrast_params = {
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
    expected_lesson = {
        "deficiency": (
            "the current harness for classification under the shared "
            f"conditions {tick_digest[:16]}"
        ),
        "situation": "evolution tick for task-1",
        "changed_component_kind": "guardrails",
        "what_changed": "v-b",
        "applicability": "classification",
        "evidence_run_ids": [
            "task-1:probe:current",
            "task-1:probe:task-1:candidate:1",
        ],
    }
    engine = LoopbackEngine(
        task_class="classification",
        decide_cases=[
            {
                "method": "harness_contrast",
                "params": contrast_params,
                "answer": {
                    "outcome": "committed",
                    "record_digest": "contrast-record-journey",
                    "payload": {
                        "adopted_version_ref": "v-b",
                        "adopted_component_kind": "guardrails",
                        "adopted_content": "tighter guardrails",
                    },
                },
            }
        ],
    )
    engine.log_payload = {
        "adoption_ref": "eg:adoption/1",
        "contrast_ref": "eg:contrast/1",
        "lesson_ref": "eg:lesson/1",
    }
    support.register_engine_decision_port(engine)

    # The host routes the adoption write to the engine: the engine
    # validates and stores the lesson for the task class.
    writes: list[dict[str, Any]] = []

    def write_envelope(write):
        writes.append(write)
        if write["kind"] == "harness_adoption":
            engine.lessons[engine.task_class] = dict(write["lesson"])

    # The improvement library returns the stored lesson once the tick
    # has persisted it.
    def library(task_class):
        stored = engine.lessons.get(task_class)
        if stored is not None:
            return [
                {
                    "component_kind": "lesson",
                    "component_version_ref": "eg:lesson/1",
                    "lesson": stored,
                }
            ]
        return [
            {"component_kind": "system_prompt", "component_version_ref": "v-a"},
            {"component_kind": "guardrails", "component_version_ref": "v-b"},
        ]

    probe_runs: list[str] = []

    async def host_probe(*, graph, config, task, budget_ref, **kwargs):
        probe_runs.append(kwargs["run_id"])
        return await execute_graph(graph, config, task, **kwargs, ports=support.PORTS)

    async def cycle_provider(request_, plan_, **kwargs):
        return await run_cycle_tick(
            request_,
            plan_,
            library_query=library,
            write_envelope=write_envelope,
            **kwargs,
        )

    support.register_harness_probe_provider(host_probe)
    support.register_harness_cycle_provider(cycle_provider)

    outcome = await drive_cycle_tick(
        request,
        plan,
        task_class="classification",
        graph=object(),
        root=tmp_path,
    )

    # The tick adopted on the served answers and switched the content.
    assert outcome.abstain_reason is None
    assert outcome.adopted_version_ref == "eg:adoption/1"
    assert outcome.contrast_ref == "eg:contrast/1"
    assert outcome.lesson_ref == "eg:lesson/1"
    assert (tmp_path / ".harness" / "guardrails.md").read_text(
        encoding="utf-8"
    ) == "tighter guardrails"
    # The host executor ran the baseline probe and one probe per
    # candidate through execute_graph; none of them started a tick.
    assert probe_runs == [
        "task-1:probe:current",
        "task-1:probe:task-1:candidate:0",
        "task-1:probe:task-1:candidate:1",
    ]
    # The adoption write carried the lesson the harness authored for
    # the tick's conditions and evidence; the progress-facts record
    # follows it through the same writer.
    assert len(writes) == 2
    assert writes[0]["adopted_version_ref"] == "v-b"
    assert writes[0]["decision_record_digest"] == "contrast-record-journey"
    assert writes[0]["conditions_digest"] == tick_digest
    assert writes[0]["lesson"] == expected_lesson
    # The engine stored the lesson for the task class.
    assert engine.lessons["classification"] == expected_lesson
    # A read through the port finds the adopted harness, the contrast
    # and the lesson.
    port = support.PORTS.engine_decision_port()
    read_answer = await port.read(
        "GetNodesByLabel", {"label": "HarnessAdoption"}, run_ref="task-1"
    )
    assert len(read_answer.payload["nodes"]) == 1
    node = read_answer.payload["nodes"][0]
    assert node["adoption_ref"] == "eg:adoption/1"
    assert node["contrast_ref"] == "eg:contrast/1"
    assert node["lesson_ref"] == "eg:lesson/1"
    # The next propose query returns the stored lesson.
    candidates = propose(
        task_ref="task-1",
        task_class="classification",
        library=library,
        seed="seed-1",
        model_ref=cycle._model_ref(plan.model),
        budget_ref=cycle._budget_ref(plan.budget),
        k=2,
    )
    assert len(candidates) == 1
    assert candidates[0].component_kind == "lesson"
    assert candidates[0].component_version_ref == "eg:lesson/1"


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R001.11")
async def test_host_registration_and_tick_trigger() -> None:
    """Without the registered provider the tick never runs; with it, the
    registered cycle provider drives one tick, and every probe run
    carries the harness_probe metadata without starting a tick of its
    own."""
    from meristem.graph import RunBudget, RunPlan
    from meristem.graph.harness_cycle import drive_cycle_tick, run_cycle_tick

    # Without a registered provider the tick fails closed: no run drives it.
    with pytest.raises(RuntimeError, match="arbora"):
        await drive_cycle_tick(
            support.request(task="classify"),
            RunPlan(model="m-1"),
            task_class="classification",
            graph=object(),
        )

    ticks: list[dict] = []
    probes: list[dict] = []

    async def host_probe(**kwargs):
        probes.append(kwargs)
        return {"run_id": kwargs["run_id"], "status": "completed", "steps": 1}

    async def cycle_provider(request, plan, **kwargs):
        ticks.append(kwargs)
        return await run_cycle_tick(
            request,
            plan,
            library_query=lambda _class: [
                {"component_kind": "system_prompt", "component_version_ref": "v-a"},
                {"component_kind": "guardrails", "component_version_ref": "v-b"},
            ],
            write_envelope=lambda envelope: "ref",
            **kwargs,
        )

    support.register_harness_probe_provider(host_probe)
    support.register_harness_cycle_provider(cycle_provider)
    request = support.request(
        task="classify", run_id="task-1", metadata={"harness_seed": "seed-1"}
    )
    outcome = await drive_cycle_tick(
        request,
        RunPlan(model="m-1", budget=RunBudget(candidate_slots=2)),
        task_class="classification",
        graph=object(),
    )

    # The tick ran exactly once, driven by the registered provider.
    assert len(ticks) == 1
    assert ticks[0]["task_class"] == "classification"
    # The probe runs carry the harness_probe metadata; none of them
    # started a tick.
    assert [call["run_id"] for call in probes] == [
        "task-1:probe:task-1:candidate:0",
        "task-1:probe:task-1:candidate:1",
    ]
    for probe in probes:
        assert probe["metadata"]["harness_probe"]["seed"] == "seed-1"
    assert outcome.abstain_reason == "measurement_unavailable"


def _adopted() -> HarnessCandidate:
    return HarnessCandidate(
        candidate_id="task-1:candidate:0",
        task_ref="task-1",
        component_kind="system_prompt",
        component_version_ref="eg:component/system_prompt/3",
        seed="seed-1",
        model_ref="model-1",
        budget_ref="budget-1",
    )


def _answer() -> ContrastAnswer:
    return ContrastAnswer(
        adopted_version_ref="eg:component/system_prompt/3",
        abstain_reason=None,
        adopted_component_kind="system_prompt",
        adopted_content="adopted prompt",
    )


def _persist_kwargs(writes: list[dict[str, Any]], root) -> dict[str, Any]:
    return {
        "task_ref": "task-1",
        "task_class": "classification",
        "decision_record_digest": "contrast-record-1",
        "conditions_digest_value": "digest-1",
        "adopted": _adopted(),
        "answer": _answer(),
        "evidence_run_ids": (
            "task-1:probe:current",
            "task-1:probe:task-1:candidate:0",
        ),
        "ports": support.PORTS,
        "run_ref": "task-1",
        "write_envelope": writes.append,
        "root": root,
    }


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R001.10")
async def test_persist_writes_adoption_through_the_bound_port(tmp_path) -> None:
    """One HarnessAdoption commit that a graph read returns."""
    engine = LoopbackEngine()
    engine.log_payload = {
        "adoption_ref": "eg:adoption/1",
        "contrast_ref": "eg:contrast/1",
        "lesson_ref": "eg:lesson/1",
    }
    support.register_engine_decision_port(engine)
    writes: list[dict[str, Any]] = []

    version_ref, contrast_ref, lesson_ref = await persist(
        **_persist_kwargs(writes, tmp_path)
    )

    assert (version_ref, contrast_ref, lesson_ref) == (
        "eg:adoption/1",
        "eg:contrast/1",
        "eg:lesson/1",
    )
    # One HarnessAdoption commit through the bound port.
    assert len(engine.commits) == 1
    assert engine.commits[0]["op"] == "HarnessAdoption"
    assert engine.commits[0]["run_ref"] == "task-1"
    # The committed adoption is readable through a graph read.
    read_answer = await engine.read(
        "GetNodesByLabel", {"label": "HarnessAdoption"}, run_ref="task-1"
    )
    assert read_answer.payload["nodes"] == [engine.commits[0]]
    # The in-run switch happened after the commit, under the run root.
    assert (tmp_path / ".harness" / "system_prompt.md").read_text(
        encoding="utf-8"
    ) == "adopted prompt"
    assert len(writes) == 2
    assert writes[1]["kind"] == "harness_evolution_progress"


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R001.10")
async def test_persist_refusals_leave_the_graph_unchanged(tmp_path) -> None:
    """Passed-through engine codes: no commit, no switch, nothing stored."""
    for code in (
        "LessonWithoutEvidence",
        "ContrastConditionsMismatch",
        "AdoptedHarnessNoDecision",
    ):
        engine = LoopbackEngine()
        engine.log_refusal_code = code
        support.register_engine_decision_port(engine)
        writes: list[dict[str, Any]] = []
        with pytest.raises(ConsumerRefused, match=code):
            await persist(**_persist_kwargs(writes, tmp_path))
        assert engine.commits == []
        assert engine.store == {}
        assert writes == []
        assert not (tmp_path / ".harness").exists()
