"""Capability designation and the sealed harness configuration (R013.x, R015.1.x, R016.1)."""

from __future__ import annotations

from dataclasses import replace

import pytest
from support.scripted_port import ScriptedPort, answer

from meristem.graph import HarnessConfig, RunPlan
from meristem.graph.state import GraphState, RunRequest
from meristem.harness import designation as dz
from meristem.harness import harness_config as hc
from meristem.harness.engine_step import digest_of
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.harness.runtime_quota import apply_runtime_quota
from meristem.host_ports import EngineDecisionRefused


def _plan() -> RunPlan:
    return RunPlan(model="m", steps=("q",), tool_names=("old",))


PAYLOAD = {
    "tools": [{"id": "t1", "why": "needed"}, {"id": "t2", "why": "also"}],
    "agents": ["a1"],
    "skills": ["s1"],
}


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R013.1")
async def test_designated_tools_replace_the_plan_tools():
    port = ScriptedPort(capability_designation=answer(PAYLOAD, digest="dd"))
    request = RunRequest("q", run_id="r1")
    plan, got = await dz.apply_designation(
        request,
        _plan(),
        port,
        task_embedding_ref="e",
        required_capabilities={"b", "a"},
        bound_tools={"t1", "t2"},
    )
    assert plan.tool_names == ("t1", "t2") and got.agents == ("a1",)
    assert got.why["t1"] == "needed" and got.record_digest == "dd"
    assert port.calls[0][2]["required_capabilities"] == ["a", "b"]
    with pytest.raises(ConsumerRefused) as raised:
        await dz.apply_designation(
            request,
            _plan(),
            port,
            task_embedding_ref="e",
            required_capabilities=(),
            bound_tools={"t1"},
        )
    assert raised.value.code == "designation_unknown_tool"
    with pytest.raises(EngineDecisionRefused):
        await dz.apply_designation(
            request, _plan(), None, task_embedding_ref="e", required_capabilities=()
        )


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R013.2")
async def test_one_outcome_per_designated_entry():
    port = ScriptedPort(capability_designation=answer(PAYLOAD, digest="dd"))
    _, got = await dz.apply_designation(
        RunRequest("q", run_id="r1"),
        _plan(),
        port,
        task_embedding_ref="e",
        required_capabilities=(),
    )
    state = GraphState(run_id="r1", query="q")
    trace = await dz.report_capability_outcomes(
        state, port, got, {"t1": True, "t2": False}
    )
    assert [(t["capability_id"], t["success"]) for t in trace] == [
        ("t1", True),
        ("t2", False),
        ("a1", None),
        ("s1", None),
    ]
    assert port.names("log") == ["capability_outcome"] * 4


def _sealed(**parts):
    return answer(parts), digest_of(parts)


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R015.1")
def test_sealed_config_is_applied_and_abstained_parts_take_defaults():
    reply, digest = _sealed(
        context_policy="elide",
        soft_threshold_q32=2**31,
        hard_threshold_q32={"abstained": True, "conservative_default": 3 * 2**30},
        recent_window_q32=2**30,
        action_space={"tool_subset": ["t1"]},
    )
    plan, applied = hc.apply_harness_config(
        _plan(), reply, harness_config_digest=digest
    )
    assert (plan.harness.soft_threshold, plan.harness.hard_threshold) == (0.5, 0.75)
    assert plan.harness.recent_window_fraction == 0.25 and applied.tool_subset == (
        "t1",
    )
    assert hc.compaction_thresholds(HarnessConfig()) == (0.6, 0.85, 0.3)
    bad, bad_digest = _sealed(soft_threshold_q32=2**31, hard_threshold_q32=2**31)
    with pytest.raises(ConsumerRefused) as raised:
        hc.apply_harness_config(_plan(), bad, harness_config_digest=bad_digest)
    assert raised.value.code == "harness_config_invalid"


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R015.1.1")
def test_digest_mismatch_stops_and_a_call_outside_the_subset_is_refused():
    reply, _ = _sealed(context_policy="none")
    with pytest.raises(ConsumerRefused) as raised:
        hc.apply_harness_config(_plan(), reply, harness_config_digest="0" * 64)
    assert raised.value.code == "HARNESS_DECISION_DIGEST_MISMATCH"
    hc.check_tool_allowed("t1", ("t1",))
    with pytest.raises(ConsumerRefused) as outside:
        hc.check_tool_allowed("other", ("t1",))
    assert outside.value.code == "TOOL_NOT_ALLOWED"


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R016.1")
async def test_quota_values_become_the_backend_limits():
    class Backend:
        limits = None

    class Workspace:
        run_id = "r1"
        backend = Backend()

    values = {"wall_time_ms": 1, "memory_bytes": 2, "process_count": 3, "grace_ms": 4}
    port = ScriptedPort(runtime_quota=[answer(values), answer({"wall_time_ms": 1})])
    workspace = Workspace()
    quota = await apply_runtime_quota(workspace, port, "build")
    assert workspace.backend.limits is quota and quota.memory_bytes == 2
    with pytest.raises(ConsumerRefused) as raised:
        await apply_runtime_quota(workspace, port, "build")
    assert raised.value.code == "quota_option_missing"
    _ = replace
