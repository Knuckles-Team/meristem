"""The sandbox liveness monitor: run-bound evidence and a guarded terminate."""

from __future__ import annotations

import asyncio

import ports_support as support
import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from support.journey import execute
from support.recorded_engine import RecordedEnginePort

from meristem.graph import HarnessConfig, RunPlan
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import ConfirmVerdict, EngineAnswer
from meristem.runtime.liveness import LivenessMonitor, liveness_interval


class Process:
    def __init__(self, process_id):
        self.process_id = process_id
        self.killed = 0

    async def alive(self):
        return True

    async def kill(self):
        self.killed += 1


class VerdictEngine(RecordedEnginePort):
    """Logs evidence; the engine answers ``terminate`` for ``target`` once asked."""

    def __init__(self, target=None):
        super().__init__({"answers": []})
        self.target = target
        self.logged = asyncio.Event()

    async def log(self, op, body, *, run_ref):
        self.bodies.append((op, dict(body)))
        payload = {}
        if self.target is not None and len(self.bodies) == 1:
            payload = {"verdict": "terminate", "process_id": self.target}
        self.logged.set()
        return EngineAnswer(outcome="advisory", payload=payload)


class Ticks:
    """An injected clock: each ``wait`` returns when the test releases one tick."""

    def __init__(self):
        self.intervals = []
        self.second_round = asyncio.Event()
        self._queue = asyncio.Queue()

    async def wait(self, seconds):
        self.intervals.append(seconds)
        if len(self.intervals) == 2:
            self.second_round.set()  # the first round ran to its end
        await self._queue.get()

    def release(self):
        self._queue.put_nowait(None)


def liveness_plan(engine, ticks, processes, *, after_tick):
    support.register_engine_decision_port(engine)
    support.PORTS.liveness_wait = ticks.wait
    support.PORTS.sandbox_processes = lambda run_id: processes

    async def respond(messages, info):
        ticks.release()
        await after_tick()
        return ModelResponse(parts=[TextPart("done")])

    return RunPlan(
        model=FunctionModel(respond),
        steps=("q",),
        harness=HarnessConfig(consumers=("sandbox_liveness",)),
    )


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R063")
async def test_evidence_is_run_bound_and_terminate_refuses_mismatch():
    engine, ticks = VerdictEngine(), Ticks()
    keep, other = Process("p1"), Process("p2")
    plan = liveness_plan(
        engine, ticks, [keep, other], after_tick=ticks.second_round.wait
    )
    result = await execute(plan, {"sandbox_liveness": {"interval_s": 45}})
    assert result["status"] == "completed"
    assert ticks.intervals == [45.0, 45.0]
    assert {(body["run_id"], body["process_id"]) for _, body in engine.bodies} == {
        ("r1", "p1"),
        ("r1", "p2"),
    }
    assert {op for op, _ in engine.bodies} == {"Evaluate"}
    assert keep.killed == other.killed == 0

    async def admit(kind, payload, run_ref):
        assert kind == "process_terminate"
        return ConfirmVerdict("admit", ref="a1")

    monitor = LivenessMonitor(engine, admit, [keep, other], run_id="r1", interval_s=30)
    for run_id, process_id in (("r2", "p1"), ("r1", "ghost")):
        with pytest.raises(ConsumerRefused, match="PROCESS_MISMATCH"):
            await monitor.terminate(run_id, process_id)
    await monitor.terminate("r1", "p1")
    assert (keep.killed, other.killed) == (1, 0)
    with pytest.raises(ConsumerRefused, match="PROCESS_MISMATCH"):
        await monitor.terminate("r1", "p1")


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R063")
async def test_a_terminate_verdict_kills_one_process_only_after_admission():
    victim, bystander = Process("p1"), Process("p2")
    engine, ticks = VerdictEngine(target="p1"), Ticks()
    asked = []

    async def confirm(kind, payload, run_ref):
        asked.append(payload["process_id"])
        return ConfirmVerdict("admit", ref="a1")

    support.PORTS.confirm = confirm
    plan = liveness_plan(
        engine, ticks, [victim, bystander], after_tick=ticks.second_round.wait
    )
    result = await execute(plan)
    assert result["status"] == "completed" and ticks.intervals == [30.0, 30.0]
    assert (victim.killed, bystander.killed) == (1, 0) and asked == ["p1"]


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R063")
@pytest.mark.parametrize(
    ("target", "verdict", "code"),
    [
        ("p1", ConfirmVerdict("deny"), "PROCESS_TERMINATE_DENIED"),
        ("ghost", ConfirmVerdict("admit"), "PROCESS_MISMATCH"),
        ("p1", None, "PROCESS_TERMINATE_CONFIRM_UNAVAILABLE"),
    ],
)
async def test_a_denied_or_mismatched_terminate_fails_the_run_and_kills_nothing(
    target, verdict, code
):
    victim, engine, ticks = Process("p1"), VerdictEngine(target=target), Ticks()

    async def confirm(kind, payload, run_ref):
        return verdict

    support.PORTS.confirm = confirm if verdict is not None else None
    plan = liveness_plan(engine, ticks, [victim], after_tick=engine.logged.wait)
    result = await execute(plan)
    assert code in result["error"] and victim.killed == 0


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R063")
async def test_an_unbound_sandbox_refuses_the_run():
    plan = RunPlan(
        model=FunctionModel(lambda m, i: ModelResponse(parts=[TextPart("x")])),
        steps=("q",),
        harness=HarnessConfig(consumers=("sandbox_liveness",)),
    )
    assert "liveness_unavailable" in (await execute(plan))["error"]


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R063")
@pytest.mark.parametrize(
    ("received", "interval"),
    [
        (None, 30.0),
        (4, 30.0),
        (601, 30.0),
        (True, 30.0),
        (5, 5.0),
        (600, 600.0),
        (90, 90.0),
    ],
)
def test_the_interval_is_the_received_value_inside_its_range(received, interval):
    assert liveness_interval(received) == interval
