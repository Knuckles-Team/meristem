"""Served-answer consumers (R029.1, R030.1, R033.1, R041.1, R046.1, R050.1, R052.1, R053.1)."""

from __future__ import annotations

import pytest
from support.scripted_port import ScriptedPort, answer

from meristem.harness import served_answers as sa
from meristem.harness import verification as vf
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineDecisionRefused


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R029.1")
async def test_replicas_run_at_chosen_width_and_irreversible_waits():
    ran: list[int] = []

    async def replica(index):
        ran.append(index)
        return index

    def port(width, verdict):
        return ScriptedPort(
            population_drift=answer(),
            fanout_width=answer({"width": width}),
            autonomy_verdict=answer({"verdict": verdict}),
        )

    common = {
        "run_id": "r1",
        "reference_window": ["a"],
        "current_window": ["b"],
        "diversity_inputs": {"refs": ["e1"]},
        "replica_ceiling": 3,
        "run_replica": replica,
    }
    got = await vf.run_replicas(port(3, "autonomous"), **common)
    assert ran == [0, 1, 2] and got["width"] == 3 and len(got["reference_digest"]) == 64
    ran.clear()
    waiting = await vf.run_replicas(port(2, "confirm"), irreversible=True, **common)
    assert ran == [] and waiting["verdict"] == "confirm"
    with pytest.raises(ConsumerRefused) as refused:
        await vf.run_replicas(port(4, "autonomous"), **common)
    assert refused.value.code == "replica_width_above_ceiling"


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R030.1")
async def test_each_trajectory_gate_and_probe_is_reported():
    port = ScriptedPort(
        trajectory_join=answer({"winner": "t1"}),
        step_verification=answer({"recovery": "retry"}),
        adversarial_admission=answer({"admitted": True}),
    )
    args = {
        "run_id": "r1",
        "trajectories": [{"id": "t1"}],
        "gates": [{"g": 1}],
        "probes": [{"id": "p1"}],
        "named_probes": ["p1"],
    }
    assert await vf.report_checks(port, **args) == {
        "winner": "t1",
        "recovery": "retry",
        "admission": True,
    }
    assert port.names("decide") == [
        "trajectory_join",
        "step_verification",
        "adversarial_admission",
    ]
    with pytest.raises(ConsumerRefused) as refused:
        await vf.report_checks(port, **{**args, "probes": [{"id": "p9"}]})
    assert refused.value.code == "probe_budget_exceeded"


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R033.1")
async def test_rank_accepts_only_a_zero_model_call_answer():
    ok = answer({"premises": ["distilled:model_calls=0"]}, options=("b", "a"))
    port = ScriptedPort(
        Rank=[
            ok,
            answer({"premises": ["distilled:model_calls=2"]}),
            answer(refusal="TOP_K_INVALID"),
        ]
    )
    assert (await sa.accept_rank(port, {"k": 2}, run_id="r1"))["order"] == ["b", "a"]
    with pytest.raises(ConsumerRefused) as refused:
        await sa.accept_rank(port, {"k": 2}, run_id="r1")
    assert refused.value.code == "distilled_contract_violated"
    with pytest.raises(ConsumerRefused) as passed:
        await sa.accept_rank(port, {"k": 2}, run_id="r1")
    assert passed.value.code == "TOP_K_INVALID"
    with pytest.raises(EngineDecisionRefused):
        await sa.accept_rank(None, {}, run_id="r1")


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R041.1")
def test_directory_per_candidate_row(tmp_path):
    rows = [
        {
            "candidate_id": f"c{i}",
            "name": f"n{i}",
            "description": "d",
            "source_digest": "x",
        }
        for i in range(3)
    ]
    written = sa.scaffold_skills(tmp_path, rows)
    assert sorted(p.parent.name for p in written) == ["c0", "c1", "c2"]
    assert "source_digest: x" in written[0].read_text()
    assert {p.name for p in tmp_path.rglob("*") if p.is_file()} == {"SKILL.md"}
    with pytest.raises(ConsumerRefused) as refused:
        sa.scaffold_skills(tmp_path, [rows[0], rows[0]])
    assert refused.value.code == "skill_candidate_duplicate"


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R046.1")
async def test_stale_epoch_stops_the_step():
    port = ScriptedPort(Loops=[answer({"epoch": 1}), answer({"epoch": 2})])
    port.queues["Loops"] = [answer({"epoch": 1}), answer({"epoch": 2})]
    done: list[int] = []

    async def request():
        done.append(1)

    with pytest.raises(ConsumerRefused) as refused:
        await sa.run_leased_step(
            port, run_id="r1", loop_id="l", lease_epoch=1, requests=[request, request]
        )
    assert refused.value.code == "loop_lease_stale" and done == [1]


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R050.1")
async def test_refused_gate_blocks_delegation():
    port = ScriptedPort(
        DecisionEval=[
            answer({"verdict": "admit"}),
            answer(refusal="DELEGATION_BELOW_THRESHOLD"),
        ]
    )
    assert await sa.admit_delegation(port, run_id="r1") == {"verdict": "admit"}
    assert port.calls[0][2] == {"arm": "DelegationQuality"}
    with pytest.raises(ConsumerRefused) as refused:
        await sa.admit_delegation(port, run_id="r1")
    assert refused.value.code == "DELEGATION_BELOW_THRESHOLD"


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R052.1")
async def test_every_transition_uses_an_engine_type_and_an_invalid_one_stops():
    port = ScriptedPort(
        AgentComponent=[answer(), answer(refusal="LIFECYCLE_TRANSITION_INVALID")]
    )
    args = {"run_id": "r1", "manifest_revision": "m1", "expected_revision": "m1"}
    assert await sa.advance_lifecycle(port, [{"type": "Activate"}], **args) == [
        "Activate"
    ]
    with pytest.raises(ConsumerRefused) as refused:
        await sa.advance_lifecycle(
            port, [{"type": "Retire"}, {"type": "Activate"}], **args
        )
    assert refused.value.code == "LIFECYCLE_TRANSITION_INVALID" and len(port.calls) == 2
    with pytest.raises(ConsumerRefused) as stale:
        await sa.advance_lifecycle(port, [], **{**args, "expected_revision": "m0"})
    assert stale.value.code == "contract_revision_stale"


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R053.1")
async def test_loop_follows_continue_or_stop():
    port = ScriptedPort(
        FeedbackLoop=[answer({"action": "continue"}), answer({"action": "stop"})]
    )
    produced = iter(["two", "three"])

    async def step():
        return next(produced)

    out = await sa.grade_loop(
        port, run_id="r1", outputs=["one"], grade=lambda text: 0.5, run_step=step
    )
    assert out == ["one", "two"] and port.calls[1][2]["key"] == "r1:1"
    with pytest.raises(ConsumerRefused) as refused:
        await sa.grade_loop(
            port, run_id="r1", outputs=["x"], grade=lambda text: None, run_step=step
        )
    assert refused.value.code == "output_ungraded"
