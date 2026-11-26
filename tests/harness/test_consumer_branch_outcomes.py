"""Submit every branch outcome, or its named gap, for fusion
(MERISTEM-HARNESS-CYCLE-R021.6)."""

from __future__ import annotations

import ports_support as support
import pytest
from support.recorded_engine import RecordedEnginePort, params_digest

from meristem.harness import plan_runtime

SPEC = "MERISTEM-HARNESS-CYCLE-R021.6"
# spec: MERISTEM-HARNESS-CYCLE-R021.6


@pytest.mark.spec(SPEC)
async def test_every_branch_submitted():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "log",
                    "params": {"op": "RunRecord"},
                    "answer": {"outcome": "solved", "record_digest": "rr-1"},
                },
                {
                    "method": "log",
                    "params": {"op": "RunRecord"},
                    "answer": {"outcome": "solved", "record_digest": "rr-2"},
                },
            ]
        }
    )
    support.register_engine_decision_port(port)
    branches = (
        plan_runtime.BranchOutcome(
            branch_id="b1", plan_digest="pd-1", outcome={"status": "done"}
        ),
        # A branch that never ran is submitted as its named gap, not omitted.
        plan_runtime.BranchOutcome(branch_id="b2", plan_digest="pd-1", gap="never_ran"),
    )

    submissions = await plan_runtime.submit_branch_outcomes(
        branches, support.PORTS.engine_decision_port(), run_ref="r1"
    )

    assert len(submissions) == 2
    assert port.calls == [
        ("log", params_digest({"op": "RunRecord"})),
        ("log", params_digest({"op": "RunRecord"})),
    ]
    # Each submission carries its branch, the plan digest and its fusion receipt.
    assert submissions[0].branch_id == "b1"
    assert submissions[0].record_digest == "rr-1"
    assert submissions[1].branch_id == "b2"
    assert submissions[1].record_digest == "rr-2"
    assert all(submission.plan_digest == "pd-1" for submission in submissions)


@pytest.mark.spec(SPEC)
def test_branch_carries_outcome_or_gap_not_both():
    with pytest.raises(ValueError):
        plan_runtime.BranchOutcome(branch_id="b", plan_digest="pd")
    with pytest.raises(ValueError):
        plan_runtime.BranchOutcome(
            branch_id="b", plan_digest="pd", outcome={"s": "d"}, gap="g"
        )
