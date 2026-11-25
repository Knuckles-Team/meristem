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

    def __init__(self, decide_cases: list[dict[str, Any]] | None = None) -> None:
        self.decide_cases = list(decide_cases or ())
        self.commits: list[dict[str, Any]] = []
        self.log_payload: dict[str, Any] = {}
        self.log_refusal_code: str | None = None
        self.store: dict[str, list[dict[str, Any]]] = {}

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
    assert len(writes) == 1


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
