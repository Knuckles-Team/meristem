"""Fan-out consumers (R021.2, R021.4, R021.5, R021.7, R021.8)."""

from __future__ import annotations

import pytest
from support.scripted_port import ScriptedPort, answer

from meristem.harness import fanout
from meristem.harness.run_lifecycle import ConsumerRefused


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R021.2")
def test_a_child_returns_a_summary_only():
    summary = fanout.ChildSummary("summary", "s", "1", "dig", 10, "tok-1")
    assert fanout.accept_child_summary(summary, max_child_summary_tokens=20) == {
        "summary_digest": "dig",
        "summary_tokens": 10,
    }
    raw = fanout.ChildSummary("transcript", "s", "1", "dig", 10, "tok-1")
    with pytest.raises(ConsumerRefused) as refused:
        fanout.accept_child_summary(raw, max_child_summary_tokens=20)
    assert refused.value.code == "child_returned_raw_output"
    with pytest.raises(ConsumerRefused):
        fanout.accept_child_summary(summary, max_child_summary_tokens=5)


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R021.4")
async def test_selected_rung_runs_width_arms_plus_the_baseline():
    port = ScriptedPort(
        trajectory_ladder=answer({"rung_id": "r2", "width": 2, "depth": 3})
    )
    seen: list[tuple[int, int]] = []

    async def arm(index, depth, snapshot="snap"):
        seen.append((index, depth))
        return fanout.ArmResult(index, snapshot, f"out{index}")

    common = {
        "run_id": "r1",
        "task_digest": "t",
        "input_snapshot_digest": "snap",
        "max_width": 4,
        "rungs": ["r1", "r2"],
    }
    trace = await fanout.run_trajectory_arms(port, run_arm=arm, **common)
    assert seen == [(0, 1), (1, 3), (2, 3)] and trace["rung_id"] == "r2"

    async def stale(index, depth):
        return fanout.ArmResult(index, "other", None)

    with pytest.raises(ConsumerRefused) as refused:
        await fanout.run_trajectory_arms(port, run_arm=stale, **common)
    assert refused.value.code == "snapshot_mismatch"


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R021.5")
async def test_one_finding_or_gap_per_analyst_role():
    port = ScriptedPort(analyst_synthesis=answer({"cited": ["a"]}))

    async def role(name):
        return [{"finding": name}] if name == "a" else []

    got = await fanout.collect_analyst_findings(
        port, run_id="r1", roles=["a", "b"], run_role=role
    )
    assert got["findings"][1] == {"role": "b", "gap": "no_finding"}
    assert got["synthesis"] == {"cited": ["a"]}

    async def twice(name):
        return [{"f": 1}, {"f": 2}]

    with pytest.raises(ConsumerRefused) as refused:
        await fanout.collect_analyst_findings(
            port, run_id="r1", roles=["a"], run_role=twice
        )
    assert refused.value.code == "analyst_role_duplicate"


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R021.7")
async def test_fanout_results_carry_receipts_or_gaps():
    port = ScriptedPort(fanout_aggregate=answer(digest="agg-1"))
    ok = fanout.FanoutSubtaskResult("t1", "ok", "rd", "rc", "rcd")
    gap = fanout.FanoutSubtaskResult("t2", "failed", failure_gap="timeout")
    assert (
        await fanout.submit_fanout_results(port, run_id="r1", results=[ok, gap])
        == "agg-1"
    )
    assert [r["task_id"] for r in port.calls[0][2]["results"]] == ["t1", "t2"]
    bare = fanout.FanoutSubtaskResult("t3", "ok", "rd")
    with pytest.raises(ConsumerRefused) as refused:
        await fanout.submit_fanout_results(port, run_id="r1", results=[bare])
    assert refused.value.code == "receipt_missing"


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R021.8")
def test_the_coordinator_delivers_exactly_one_final_artifact():
    decl = {"schema_id": "s", "schema_version": "1", "kind": "report"}
    art = {**decl, "digest": "d"}
    assert fanout.run_coordinator("out", [art], decl) == {
        "coordinator": "out",
        "digest": "d",
    }
    for artifacts in ([], [art, art], [{**art, "kind": "other"}]):
        with pytest.raises(ConsumerRefused) as refused:
            fanout.run_coordinator("out", artifacts, decl)
        assert refused.value.code == "final_artifact_count"
