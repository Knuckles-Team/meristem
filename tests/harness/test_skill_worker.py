"""Skill worker summary (MERISTEM-HARNESS-CYCLE-R095)."""

from __future__ import annotations

import pytest

from meristem.harness.skill_worker import (
    WorkerModelNotAllowed,
    WorkerPlan,
    run_skill_in_worker,
)


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R095")
async def test_summary_bounded_background_delivery_and_model_refusal():
    calls = []

    async def child(model, ref, lineage):
        calls.append((model, ref, lineage))
        return "x" * 100

    delivered = []

    async def deliver(summary):
        delivered.append(summary)

    plan = WorkerPlan("small", ("small",), summary_max_tokens=5)
    summary = await run_skill_in_worker(
        "s/a", plan, parent_run_id="r1", run_child=child, deliver=deliver
    )
    assert summary.truncated and len(summary.text) == 20
    assert calls[0] == ("small", "s/a", {"parent_run_id": "r1", "skill_ref": "s/a"})

    background = WorkerPlan(
        "small", ("small",), mode="background", summary_max_tokens=50
    )
    task = await run_skill_in_worker(
        "s/a", background, parent_run_id="r1", run_child=child, deliver=deliver
    )
    await task
    assert len(delivered) == 1 and not delivered[0].truncated

    with pytest.raises(WorkerModelNotAllowed) as refused:
        await run_skill_in_worker(
            "s/a",
            WorkerPlan("big", ("small",)),
            parent_run_id="r1",
            run_child=child,
            deliver=deliver,
        )
    assert refused.value.code == "WORKER_MODEL_NOT_ALLOWED"
    assert len(calls) == 2
