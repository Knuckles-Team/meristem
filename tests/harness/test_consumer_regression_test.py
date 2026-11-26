"""A regression test on both revisions (MERISTEM-SCM-R007.1)."""

from __future__ import annotations

import ports_support as support
import pytest
from support.coding_fakes import Gates, Heads, ScriptedEngine, tool_plan

from meristem.graph.executor import iterate_run
from meristem.graph.state import CodingConfig

SPEC = "MERISTEM-SCM-R007.1"
# spec: MERISTEM-SCM-R007.1
CONFIG = CodingConfig(regression_command="pytest new")
D1, D2, DF = "a" * 64, "b" * 64, "c" * 64
ARGS = {
    "trace_id": "tr",
    "failure_point": "step 3",
    "fix_id": "fx",
    "changeset_digest": DF,
    "test_id": "tt",
    "before_head": D1,
    "after_head": D2,
}


async def _run(plan):
    return [e async for e in iterate_run(support.request(task="t"), plan=plan)]


def _bind(answer):
    support.reset()
    heads = Heads()
    outcomes = iter([(1, ""), (0, "")])

    class Seq(Gates):
        async def run_gate(self, workspace, command):
            self.results[command] = next(outcomes)
            return await super().run_gate(workspace, command)

    gates = Seq({})
    engine = ScriptedEngine({"decide": [answer]})
    support.PORTS.changeset_submitter = lambda run_id: heads
    support.PORTS.gate_runner = lambda run_id: gates
    support.register_engine_decision_port(engine)
    return heads, gates, engine


@pytest.mark.spec(SPEC)
async def test_test_runs_on_both_revisions():
    heads, gates, engine = _bind({"outcome": "advisory", "option_ids": ("admit",)})
    seen: list = []
    await _run(tool_plan("bind_correction", ARGS, seen, CONFIG))
    assert heads.heads == [D1, D2] and gates.commands == ["pytest new"] * 2
    assert seen == [{"admitted": True}]
    (kind, binding) = engine.calls[0][1]
    assert kind == "CorrectionAdmission"
    assert [r["outcome"] for r in binding["receipts"]] == ["failed", "passed"]
    assert binding["test"]["before_revision"] == D1


@pytest.mark.spec(SPEC)
async def test_refused_binding_is_not_admitted():
    _bind({"outcome": "abstained", "refusal_code": "BINDING_CHAIN_BROKEN"})
    seen: list = []
    await _run(tool_plan("bind_correction", ARGS, seen, CONFIG))
    assert seen == [{"refused": "BINDING_CHAIN_BROKEN"}]
    seen.clear()
    _bind({"outcome": "advisory", "option_ids": ("reject",)})
    await _run(tool_plan("bind_correction", ARGS, seen, CONFIG))
    assert seen == [{"admitted": False}]
