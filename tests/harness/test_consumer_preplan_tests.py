"""Pre-plan receipt before planning (MERISTEM-SCM-R004.1)."""

from __future__ import annotations

import ports_support as support
import pytest
from support.coding_fakes import Gates, Heads, ScriptedEngine, tool_plan

from meristem.graph.executor import iterate_run
from meristem.graph.state import CodingConfig

SPEC = "MERISTEM-SCM-R004.1"
# spec: MERISTEM-SCM-R004.1
CONFIG = CodingConfig(preplan_command="make test", preplan_artifacts=("out.bin",))


async def _run(plan):
    return [e async for e in iterate_run(support.request(task="t"), plan=plan)]


def _bind(code: int):
    support.reset()
    events: list = []
    support.PORTS.events = events.append
    heads = Heads()
    gates = Gates({"make test": (code, "ok")}, files={"out.bin": "bytes"})
    engine = ScriptedEngine(
        {
            "log": [{"outcome": "committed", "record_digest": "rec"}],
        }
    )
    support.PORTS.changeset_submitter = lambda run_id: heads
    support.PORTS.gate_runner = lambda run_id: gates
    support.register_engine_decision_port(engine)
    return engine, events


@pytest.mark.spec(SPEC)
async def test_receipt_submitted_before_planning():
    engine, events = _bind(0)
    seen: list = []
    await _run(tool_plan("run_preplan", {}, seen, CONFIG))
    assert [c[0] for c in engine.calls] == ["log"]
    (result,) = seen
    receipt = result["receipt"]
    assert result["reusable"] is True and receipt["artifact_count"] == 1
    assert receipt["started_at_ms"] <= receipt["completed_at_ms"]
    assert receipt["repository_revision"] == "head-0"
    assert [e for e in events if e.get("kind") == "preplan_receipt_committed"]


@pytest.mark.spec(SPEC)
async def test_failed_command_is_not_reusable_and_missing_command_skips():
    _bind(1)
    seen: list = []
    await _run(tool_plan("run_preplan", {}, seen, CONFIG))
    assert seen[0]["reusable"] is False and seen[0]["receipt"]["exit_code"] == 1
    engine, _ = _bind(0)
    seen.clear()
    await _run(tool_plan("run_preplan", {}, seen, CodingConfig()))
    assert seen == [{"reusable": False, "receipt": None}] and engine.calls == []
