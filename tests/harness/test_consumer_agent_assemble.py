"""Apply the assembled primitive composition to the resolved plan
(MERISTEM-HARNESS-CYCLE-R009.1)."""

from __future__ import annotations

from dataclasses import dataclass

import ports_support as support
import pytest
from support.recorded_engine import RecordedEnginePort, params_digest

from meristem.harness import assembly
from meristem.harness.run_lifecycle import ConsumerRefused

SPEC = "MERISTEM-HARNESS-CYCLE-R009.1"
# spec: MERISTEM-HARNESS-CYCLE-R009.1


@dataclass(frozen=True)
class MiniPlan:
    """The smallest plan the composition replace can carry."""

    steps: tuple[str, ...]
    tool_names: tuple[str, ...] = ()


REQUEST_PARAMS = {"task": "t", "run_id": "r1", "mode": "ask", "budget_tokens": None}
PRIMITIVES = [
    {"kind": "prompt", "name": "p1"},
    {"kind": "tool", "name": "t1"},
    {"kind": "memory", "name": "m1"},
    {"kind": "control", "name": "c1"},
]


@pytest.mark.spec(SPEC)
async def test_composition_applied_through_port():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "assemble",
                    "params": REQUEST_PARAMS,
                    "answer": {
                        "outcome": "solved",
                        "payload": {"primitives": PRIMITIVES},
                        "record_digest": "rec-1",
                    },
                }
            ]
        }
    )
    support.register_engine_decision_port(port)
    plan = MiniPlan(steps=("old",))

    resolved = await assembly.assemble_plan(
        support.request(task="t", run_id="r1"),
        plan,
        support.PORTS.engine_decision_port(),
        run_ref="r1",
    )

    assert port.calls == [("assemble", params_digest(REQUEST_PARAMS))]
    # Exactly the received primitives, in received order: nothing added, nothing dropped.
    assert resolved.steps == ("p1", "t1", "m1", "c1")
    assert resolved.tool_names == ("t1",)


@pytest.mark.spec(SPEC)
async def test_primitive_outside_the_closed_kinds_refuses():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "assemble",
                    "params": REQUEST_PARAMS,
                    "answer": {
                        "outcome": "solved",
                        "payload": {"primitives": [{"kind": "web", "name": "w"}]},
                    },
                }
            ]
        }
    )
    support.register_engine_decision_port(port)
    plan = MiniPlan(steps=("old",))

    with pytest.raises(ConsumerRefused, match="composition_unsupported"):
        await assembly.assemble_plan(
            support.request(task="t", run_id="r1"),
            plan,
            support.PORTS.engine_decision_port(),
            run_ref="r1",
        )


@pytest.mark.spec(SPEC)
async def test_unbound_port_keeps_the_plan_unchanged():
    plan = MiniPlan(steps=("old",))

    resolved = await assembly.assemble_plan(
        support.request(task="t", run_id="r1"),
        plan,
        support.PORTS.engine_decision_port(),
        run_ref="r1",
    )

    assert resolved is plan
