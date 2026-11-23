"""The probe status is the closed run state machine, not a string copy.

R087: ``ProbeTrace.status`` is the ``RunStatus`` enum of R084 and the stop
reason is derived from it. A value outside the closed machine refuses the
probe with ``RUN_STATE_UNKNOWN`` — the same typed code the checkpoint
restore path uses.
"""

from __future__ import annotations

import ports_support as support
import pytest

from meristem.graph.checkpoint import RUN_STATE_UNKNOWN, CheckpointUnavailable
from meristem.graph.harness_cycle import probe_candidates
from meristem.graph.state import RunStatus

SPEC = "MERISTEM-HARNESS-CYCLE-R087"


def _provider_for(status: str):
    async def provider(*, run_id, **kwargs):
        return {
            "run_id": run_id,
            "status": status,
            "steps": 1,
            "usage": {"requests": 1, "input_tokens": 5, "output_tokens": 3},
        }

    return provider


async def _probe(status: str) -> tuple:
    support.register_harness_probe_provider(_provider_for(status))
    return await probe_candidates(
        graph=object(),
        config=None,
        task="classify",
        seed="seed-1",
        model_ref="model-1",
        budget_ref="budget-1",
        run_id="run-1",
        current_version_ref="v-0",
        candidates=(),
        ports=support.PORTS,
    )


# spec: MERISTEM-HARNESS-CYCLE-R087
@pytest.mark.spec(SPEC)
@pytest.mark.parametrize(
    ("stored", "expected", "reason"),
    [
        ("pending", RunStatus.PENDING, None),
        ("running", RunStatus.RUNNING, None),
        ("cancelling", RunStatus.CANCELLING, None),
        ("completed", RunStatus.COMPLETED, None),
        ("failed", RunStatus.FAILED, None),
        ("stopped_requested", RunStatus.STOPPED_REQUESTED, "requested"),
        ("stopped_budget", RunStatus.STOPPED_BUDGET, "budget"),
        ("stopped_cancelled", RunStatus.STOPPED_CANCELLED, "cancelled"),
    ],
)
async def test_probe_status_is_the_run_status_enum(
    stored: str, expected: RunStatus, reason: str | None
) -> None:
    traces = await _probe(stored)
    assert len(traces) == 1
    assert traces[0].status is expected
    assert traces[0].status.stop_reason == reason


@pytest.mark.spec(SPEC)
async def test_unknown_probe_status_is_refused_with_run_state_unknown() -> None:
    # The refusal is the typed code, raised before the trace wrapper, so a
    # value outside the machine never lands in a ProbeTrace.
    with pytest.raises(CheckpointUnavailable) as caught:
        await _probe("paused")
    assert caught.value.code == RUN_STATE_UNKNOWN
