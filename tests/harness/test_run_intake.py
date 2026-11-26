"""Replay intake, workspace projection and the retrieval step (MERISTEM-RUN-R004.1, R006.1, R010, R011)."""

from __future__ import annotations

import pytest

from meristem.harness.replay_intake import admit_replay_events
from meristem.harness.retrieval_step import read_discover_verdict, run_retrieval_round
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import HostPorts, ToolStepResult
from meristem.runtime.events import FileReadAction, FileWriteAction
from meristem.runtime.local_workspace import LocalWorkspace
from meristem.runtime.workspace import DevWorkspace, Projection


def _event(seq: int, event_id: str | None = None, **over):
    return {
        "event_id": event_id or f"e{seq}",
        "seq": seq,
        "event_time_ms": 1000 + seq,
        "snapshot": "s",
        "action_id": f"a{seq}",
        "outcome_refs": [],
        "source_revision": "rev",
        **over,
    }


@pytest.mark.spec("MERISTEM-RUN-R006.1")
def test_replay_envelope_is_checked_and_duplicates_are_idempotent():
    got = admit_replay_events([_event(1), _event(2), _event(2)])
    assert [e["seq"] for e in got] == [1, 2]
    cases = [
        ([{**_event(1), "extra": 1}], "REPLAY_ENVELOPE_MALFORMED"),
        (
            [{k: v for k, v in _event(1).items() if k != "snapshot"}],
            "REPLAY_ENVELOPE_MALFORMED",
        ),
        ([_event(1), _event(3)], "REPLAY_SEQUENCE_GAP"),
        ([_event(2), _event(1)], "REPLAY_OUT_OF_ORDER"),
        (
            [_event(1), _event(2), _event(2, snapshot="other")],
            "REPLAY_DUPLICATE_CONFLICT",
        ),
    ]
    for events, code in cases:
        with pytest.raises(ConsumerRefused) as refused:
            admit_replay_events(events)
        assert refused.value.code == code


@pytest.mark.spec("MERISTEM-RUN-R004.1")
async def test_withheld_path_read_rejected(tmp_path):
    async with DevWorkspace(LocalWorkspace(root=tmp_path), run_id="r") as workspace:
        workspace.apply_projection(
            Projection(allowed=frozenset({"src"}), withheld=frozenset({"src/secret"}))
        )
        ok = await workspace.act(FileWriteAction(path="src/a.txt", content="hi"))
        assert getattr(ok, "message", "") != "WORKSPACE_PATH_WITHHELD"
        assert (await workspace.act(FileReadAction(path="src/a.txt"))).content == "hi"
        for path in ("src/secret/k.txt", "other/b.txt"):
            for action in (
                FileReadAction(path=path),
                FileWriteAction(path=path, content="x"),
            ):
                obs = await workspace.act(action)
                assert obs.message == "WORKSPACE_PATH_WITHHELD"
        assert not (tmp_path / "other").exists()


def _ports(answers: dict[str, object]) -> tuple[HostPorts, list]:
    calls: list = []

    async def caller(server, tool, arguments, task_id):
        calls.append((tool, arguments))
        value = answers[tool]
        return (
            value if isinstance(value, ToolStepResult) else ToolStepResult(result=value)
        )

    return HostPorts(caller=caller), calls


@pytest.mark.spec("MERISTEM-RUN-R010")
async def test_step_applies_round_verdicts():
    ports, calls = _ports(
        {
            "eg.query.RetrievalRound": {
                "sufficiency": {"repair": "widen"},
                "stop": {"stop": True},
                "arbitration": {"choice": "Abstain"},
            }
        }
    )
    outcome = await run_retrieval_round(
        ports, plan={"q": 1}, counters={"round": 2}, prior={"id": "c"}, run_id="r1"
    )
    assert (outcome.repair, outcome.stop, outcome.abstained) == ("widen", True, True)
    assert calls[0][1]["request"] == {
        "plan": {"q": 1},
        "state": {"round": 2},
        "arbitration": {"id": "c"},
    }
    with pytest.raises(ConsumerRefused) as unbound:
        await run_retrieval_round(HostPorts(), plan={}, counters={}, run_id="r1")
    assert unbound.value.code == "tool_caller_unavailable"
    refused, _ = _ports(
        {
            "eg.query.RetrievalRound": ToolStepResult(
                outcome="refused", error="ROUND_STALE"
            )
        }
    )
    with pytest.raises(ConsumerRefused) as stale:
        await run_retrieval_round(refused, plan={}, counters={}, run_id="r1")
    assert stale.value.code == "ROUND_STALE"


@pytest.mark.spec("MERISTEM-RUN-R011")
async def test_failed_verdict_is_reported_not_recomputed():
    ports, _ = _ports(
        {
            "eg.ingestion.Discover": {
                "verdict": {"gate_passed": False, "failure_modes": ["sparse", "stale"]}
            }
        }
    )
    report = await read_discover_verdict(ports, request={"q": 1}, run_id="r1")
    assert report.gate_passed is False and report.failure_modes == ("sparse", "stale")


@pytest.mark.spec("MERISTEM-RUN-R010")
@pytest.mark.spec("MERISTEM-RUN-R011")
async def test_retrieval_consumers_run_before_the_first_model_step(events):
    import ports_support as support
    from pydantic_ai.models.test import TestModel

    from meristem.graph import HarnessConfig, RunPlan, create_graph_agent, execute_graph

    async def caller(server, tool, arguments, task_id):
        body = {
            "eg.query.RetrievalRound": {"stop": {"stop": True}},
            "eg.ingestion.Discover": {"verdict": {"gate_passed": True}},
        }[tool]
        return ToolStepResult(result=body)

    support.PORTS.caller = caller
    plan = RunPlan(
        model=TestModel(custom_output_text="done"),
        steps=("q",),
        harness=HarnessConfig(consumers=("retrieval_round", "discover_verdict")),
    )
    support.register_run_decision_provider(lambda request: plan)
    graph, config = create_graph_agent(agent_model="m", ports=support.PORTS)
    result = await execute_graph(
        graph,
        config,
        "q",
        run_id="r1",
        ports=support.PORTS,
        metadata={
            "retrieval_round": {"plan": {"q": 1}},
            "discover_verdict": {"q": 1},
        },
    )
    assert result["status"] == "completed"
    kinds = [e["type"] for e in events]
    assert "retrieval_round_applied" in kinds and "discover_verdict_read" in kinds
