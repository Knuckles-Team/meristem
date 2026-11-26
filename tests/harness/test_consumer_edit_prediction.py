"""One edit round with a declared prediction (MERISTEM-SCM-R005.1)."""

from __future__ import annotations

import ports_support as support
import pytest
from support.coding_fakes import Gates, Heads, ScriptedEngine, tool_plan

from meristem.graph.executor import iterate_run
from meristem.graph.state import CodingConfig

SPEC = "MERISTEM-SCM-R005.1"
# spec: MERISTEM-SCM-R005.1
CONFIG = CodingConfig(tasks={"t1": "run t1"})
EDITS = [{"path": "a.txt", "search": "x", "replace": "y"}]
PREDICTION = {"round": 1, "fixes": ["t1"], "regressions": []}


async def _run(plan):
    return [e async for e in iterate_run(support.request(task="t"), plan=plan)]


def _bind(answer):
    support.reset()
    heads = Heads()
    gates = Gates({"run t1": (0, "")})
    engine = ScriptedEngine({"decide": [answer]})
    support.PORTS.changeset_submitter = lambda run_id: heads
    support.PORTS.gate_runner = lambda run_id: gates
    support.register_engine_decision_port(engine)
    return heads, gates, engine


@pytest.mark.spec(SPEC)
async def test_edit_reverted_on_failed_prediction():
    args = {"edits": EDITS, "prediction": PREDICTION}
    seen: list = []
    heads, gates, engine = _bind({"outcome": "advisory", "option_ids": ("verified",)})
    await _run(tool_plan("run_edit_round", args, seen, CONFIG))
    assert seen == ["kept"] and heads.heads == ["head-0"]
    (kind, params) = engine.calls[0][1]
    assert kind == "RoundVerdict" and params["prediction"] == PREDICTION
    assert params["outcomes"] == [
        {"task_id": "t1", "baseline_pass": True, "after_pass": True}
    ]
    for option in ("falsified", "unverified"):
        seen.clear()
        heads, _, _ = _bind({"outcome": "advisory", "option_ids": (option,)})
        await _run(tool_plan("run_edit_round", args, seen, CONFIG))
        assert seen == ["reverted"] and heads.heads == ["head-0", "head-0"]
    seen.clear()
    _bind({"outcome": "abstained", "refusal_code": "ROUND_NO_PREDICTION"})
    await _run(tool_plan("run_edit_round", args, seen, CONFIG))
    assert seen == ["reverted"]


@pytest.mark.spec(SPEC)
async def test_malformed_prediction_applies_nothing():
    heads, gates, engine = _bind({"outcome": "advisory"})
    seen: list = []
    bad = {"edits": EDITS, "prediction": {"round": 1, "fixes": ["t1"]}}
    await _run(tool_plan("run_edit_round", bad, seen, CONFIG))
    assert seen == ["prediction_malformed"]
    assert gates.applied == [] and gates.commands == [] and engine.calls == []
