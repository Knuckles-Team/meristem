"""Staffing follows the served recruit and allocate answers; the instance drains."""

from __future__ import annotations

import ports_support as support
import pytest
from support.journey import execute, text_plan
from support.recorded_engine import RecordedEnginePort

RECRUIT = {"role": "reviewer", "need": 2}
ALLOCATE = {"budget": 5}
META = {
    "org_runtime": {"recruit": RECRUIT, "allocate": ALLOCATE, "instance_id": "inst-1"}
}


def entry(method, params, **answer):
    return {"method": method, "params": params, "answer": answer}


def served(*, refuse=None):
    answers = [
        entry(
            "recruit",
            RECRUIT,
            outcome="solved",
            option_ids=["agent-b", "agent-a"],
            **({"refusal_code": refuse} if refuse else {}),
        ),
        entry(
            "allocate",
            {**ALLOCATE, "candidates": ["agent-b", "agent-a"]},
            outcome="solved",
            payload={"assignments": {"agent-b": 3, "agent-a": 2}},
        ),
        entry(
            "Statechart",
            {"instance_id": "inst-1", "event": "staffed"},
            outcome="committed",
            payload={"state": "staffed"},
        ),
        entry(
            "Statechart",
            {"instance_id": "inst-1", "event": "drain"},
            outcome="committed",
            payload={"state": "drained"},
        ),
    ]
    port = RecordedEnginePort({"answers": answers})
    support.register_engine_decision_port(port)
    return port


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R074")
async def test_staffing_follows_recruit_and_allocate(events):
    port = served()
    result = await execute(text_plan(["org_runtime"]), META)
    assert result["status"] == "completed"
    assert [call[0] for call in port.calls] == [
        "recruit",
        "allocate",
        "Statechart",
        "Statechart",
    ]
    (staffed,) = [e for e in events if e.get("type") == "staffing_applied"]
    assert staffed["recruited"] == ["agent-b", "agent-a"]
    assert staffed["allocation"] == {"assignments": {"agent-b": 3, "agent-a": 2}}
    assert staffed["instance_state"] == "staffed"
    (drained,) = [e for e in events if e.get("type") == "instance_drained"]
    assert drained["instance_state"] == "drained"


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R074")
async def test_a_refused_recruit_or_unbound_port_fails_the_run():
    served(refuse="NO_CANDIDATES")
    refused = await execute(text_plan(["org_runtime"]), META)
    assert refused["status"] == "failed" and "NO_CANDIDATES" in refused["error"]
    support.register_engine_decision_port(None)
    unbound = await execute(text_plan(["org_runtime"]), META)
    assert "engine_decision_unavailable" in unbound["error"]
