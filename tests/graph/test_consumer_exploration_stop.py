"""Stop an exploration loop on exhausted budget
(MERISTEM-HARNESS-CYCLE-R031.1)."""

from __future__ import annotations

import inspect

import pytest

from meristem import host_ports as hp
from meristem.graph import budget
from meristem.harness.run_lifecycle import ConsumerRefused

SPEC = "MERISTEM-HARNESS-CYCLE-R031.1"


def _response(remaining: int) -> hp.EngineAnswer:
    return hp.EngineAnswer(
        outcome="solved",
        payload={
            "cost_report": {
                "cost_units": 10,
                "remaining_units": remaining,
                "rows": 5,
                "visits": 2,
            }
        },
    )


@pytest.mark.spec(SPEC)
def test_exhausted_budget_stops_without_retry():
    with pytest.raises(ConsumerRefused, match="retrieval_budget_exhausted"):
        budget.stop_on_exhausted_retrieval(_response(remaining=0))
    # The consumer reads the one served response it is given: it carries no
    # port and no retry, so no larger request can be issued.
    assert (
        "port" not in inspect.signature(budget.stop_on_exhausted_retrieval).parameters
    )


@pytest.mark.spec(SPEC)
def test_cost_report_is_read_from_the_served_response():
    report = budget.stop_on_exhausted_retrieval(_response(remaining=4))

    assert (report.cost_units, report.remaining_units, report.rows, report.visits) == (
        10,
        4,
        5,
        2,
    )
    assert report.exhausted is False


@pytest.mark.spec(SPEC)
def test_engine_refusal_passes_through():
    response = hp.EngineAnswer(
        outcome="abstained", refusal_code="RETRIEVAL_DENIED", payload={}
    )
    with pytest.raises(ConsumerRefused, match="RETRIEVAL_DENIED"):
        budget.stop_on_exhausted_retrieval(response)


@pytest.mark.spec(SPEC)
def test_response_without_a_cost_report_is_a_contract_failure():
    response = hp.EngineAnswer(outcome="solved", payload={})
    with pytest.raises(ValueError, match="cost report"):
        budget.stop_on_exhausted_retrieval(response)
