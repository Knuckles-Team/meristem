"""Record evolution proposal progress facts (MERISTEM-HARNESS-CYCLE-R064)."""

from __future__ import annotations

from typing import Any

import ports_support as support
import pytest
from support.recorded_engine import RecordedEnginePort

from meristem.graph.harness_cycle import ContrastAnswer, HarnessCandidate, persist


async def _persist(writes: list[dict[str, Any]], root) -> None:
    """One persist over a recorded medulla fixture, writing every envelope."""
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "log",
                    "params": {"op": "HarnessAdoption"},
                    "answer": {
                        "outcome": "committed",
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
    adopted = HarnessCandidate(
        candidate_id="task-1:candidate:1",
        task_ref="task-1",
        component_kind="guardrails",
        component_version_ref="v-b",
        seed="seed-1",
        model_ref="model-1",
        budget_ref="budget-1",
    )
    answer = ContrastAnswer(
        adopted_version_ref="v-b",
        abstain_reason=None,
        adopted_component_kind="guardrails",
        adopted_content="tighter guardrails",
    )

    await persist(
        task_ref="task-1",
        task_class="classification",
        decision_record_digest="contrast-record-1",
        conditions_digest_value="digest-1",
        adopted=adopted,
        answer=answer,
        evidence_run_ids=(
            "task-1:probe:current",
            "task-1:probe:task-1:candidate:1",
        ),
        ports=support.PORTS,
        run_ref="task-1",
        write_envelope=writes.append,
        root=root,
    )


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R064")
async def test_persist_step_records_ordered_run_ids(tmp_path) -> None:
    """The persist step records the evolution proposal's progress facts:
    the proposal id and revision, the prompt version and revision, the
    ordered source run identifiers, state and observation time — through
    the envelope writer, with no new store."""
    writes: list[dict[str, Any]] = []
    await _persist(writes, tmp_path)

    # One adoption envelope, then one progress-facts envelope, in order.
    assert len(writes) == 2
    assert writes[0]["kind"] == "harness_adoption"
    progress = writes[1]
    assert progress["kind"] == "harness_evolution_progress"
    assert progress["proposal_id"] == "task-1"
    assert progress["proposal_revision"] == 1
    assert progress["prompt_version_id"] == "v-b"
    assert progress["prompt_revision"] == 1
    # The ordered source run identifiers: the baseline probe first, then
    # the adopted candidate's probe — the order medulla joins to the
    # run trace identifiers.
    assert progress["source_run_ids"] == [
        "task-1:probe:current",
        "task-1:probe:task-1:candidate:1",
    ]
    assert progress["state"] == "adopted"
    assert progress["observed_at"] > 0
