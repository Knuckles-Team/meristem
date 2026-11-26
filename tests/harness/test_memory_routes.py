"""Memory routes and trajectory consumers (R035.1, R038.1, R039.1, R040.1, R048.1 to R048.4)."""

from __future__ import annotations

import pytest
from support.scripted_port import ScriptedPort, answer

from meristem.graph.state import GraphState, RunStatus
from meristem.harness import memory_routes as mr
from meristem.harness import trajectory as tj
from meristem.harness.run_lifecycle import ConsumerRefused


def _state(status=RunStatus.COMPLETED) -> GraphState:
    state = GraphState(run_id="r1", query="q")
    state.status = status
    return state


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R035.1")
async def test_only_applied_notes_injected_and_one_capture_per_successful_run():
    notes = [{"note_id": "n1", "applied": True}, {"note_id": "n2", "applied": False}]
    port = ScriptedPort(MemoryRecall=answer({"notes": notes}))
    assert [n["note_id"] for n in await mr.recall_applied_notes(port, run_id="r1")] == [
        "n1"
    ]
    state = _state()
    got = await mr.capture_session_note(port, state, {"note_id": "n1", "text": "t"})
    assert got is not None and got["note:run"] == "r1"
    assert await mr.capture_session_note(port, state, {"note_id": "n1"}) is None
    assert port.names("log") == ["RecordSessionNote"]
    with pytest.raises(ConsumerRefused) as refused:
        await mr.capture_session_note(port, _state(RunStatus.FAILED), {})
    assert refused.value.code == "note_run_not_successful"


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R038.1")
async def test_recall_at_start_and_seal_at_end():
    port = ScriptedPort(
        MemoryRecall=answer(
            {"episodes": [{"session_id": "s", "transcript_revision": "v1"}]}
        ),
        MemoryRemember=answer(),
    )
    episodes = await mr.recall_episodes(port, run_id="r1", as_of_ms=5)
    assert port.calls[0][2] == {"scope": "Episodes", "as_of_ms": 5}
    episode_id = await mr.seal_episode(
        port, run_id="r1", session_id="s", transcript_revision="v1", recalled=episodes
    )
    assert port.calls[1][2]["episode_spec"] == {
        "episode_id": episode_id,
        "session_id": "s",
    }
    for kwargs, code in (
        ({"session_id": None}, "session_id_missing"),
        ({"session_id": "s", "transcript_revision": "v2"}, "episode_revision_stale"),
    ):
        args = {
            "run_id": "r1",
            "transcript_revision": "v1",
            "recalled": episodes,
            **kwargs,
        }
        with pytest.raises(ConsumerRefused) as refused:
            await mr.seal_episode(port, **args)
        assert refused.value.code == code


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R040.1")
async def test_plan_applied_only_after_approval_and_only_through_five_routes():
    port = ScriptedPort(
        Consolidate=answer(), Reinforce=answer(), DecayMemories=answer()
    )
    plan = [
        {"route": "Consolidate"},
        {"route": "Reinforce"},
        {"route": "DecayMemories"},
    ]
    with pytest.raises(ConsumerRefused) as refused:
        await mr.apply_plan(port, plan, run_id="r1", approval_ref=None)
    assert refused.value.code == "consolidation_unapproved" and port.calls == []
    applied = await mr.apply_plan(port, plan, run_id="r1", approval_ref="appr")
    assert port.names() == ["Consolidate", "Reinforce", "DecayMemories"]
    assert applied[0]["approval_ref"] == "appr"
    with pytest.raises(ConsumerRefused) as forbidden:
        await mr.apply_plan(
            port, [{"route": "DeleteAll"}], run_id="r1", approval_ref="a"
        )
    assert forbidden.value.code == "memory_route_forbidden"
    with pytest.raises(ConsumerRefused):
        await mr.memory_call(port, "Loops", {}, run_id="r1")


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R039.1")
async def test_default_breadth_on_abstention_and_one_learn_per_branch():
    port = ScriptedPort(
        BranchOutcome=[
            answer({"breadth": 2}),
            answer(refusal="NO_OUTCOME_HISTORY"),
            answer(refusal="BREADTH_LIMIT_EXCEEDED"),
        ]
    )
    args = {"run_id": "r1", "max_concurrency": 4}
    assert await tj.plan_breadth(port, {}, **args) == 2
    assert await tj.plan_breadth(port, {}, **args) == 4
    with pytest.raises(ConsumerRefused):
        await tj.plan_breadth(port, {}, **args)

    async def branch(index):
        return {"branch": index}

    await tj.run_wave(port, run_id="r1", decision_id="d", breadth=2, run_branch=branch)
    assert port.names("log") == ["Learn"] * 3


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R048.1")
async def test_every_request_carries_budget_decision():
    port = ScriptedPort(TaskPlan=answer({"decision_id": "dec-1", "count": 5}))
    budget = await tj.bind_budget(port, {}, run_id="r1")
    assert budget.tag({"n": 1}) == {"n": 1, "decision_id": "dec-1"}
    with pytest.raises(ConsumerRefused) as refused:
        tj.TrajectoryBudget("", 1).tag({})
    assert refused.value.code == "budget_decision_unbound"


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R048.2")
def test_projection_applied_and_candidate_reinserted():
    order, digest = tj.apply_projection(
        ["a", "b"],
        {
            "order": ["b", "a"],
            "candidate": "c",
            "position": 1,
            "projection_digest": "p",
        },
    )
    assert order == ["b", "c", "a"] and digest == "p"


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R048.3")
async def test_breadth_applied_and_receipt_returned():
    port = ScriptedPort()
    started: list[int] = []

    async def branch(index):
        started.append(index)
        return {"i": index}

    await tj.run_wave(port, run_id="r1", decision_id="d", breadth=3, run_branch=branch)
    assert started == [0, 1, 2]
    assert port.blobs[-1] == {
        "decision_id": "d",
        "branch_count": 3,
        "outcomes": [{"i": 0}, {"i": 1}, {"i": 2}],
    }


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R048.4")
async def test_comparisons_and_revisions_bounded():
    port = ScriptedPort(pairwise_verdict=answer())
    revised: list[str] = []

    async def compare(left, right):
        return {"winner": left}

    async def revise(item):
        revised.append(item)

    args = {
        "run_id": "r1",
        "pairs": [("a", "b"), ("a", "c")],
        "compare": compare,
        "revise": revise,
    }
    verdicts = await tj.run_pairwise(port, revision_bound=1, revisions=["a"], **args)
    assert [v["pair"] for v in verdicts] == [["a", "b"], ["a", "c"]] and revised == [
        "a"
    ]
    with pytest.raises(ConsumerRefused) as refused:
        await tj.run_pairwise(port, revision_bound=1, revisions=["a", "b"], **args)
    assert refused.value.code == "revision_bound_exceeded"
