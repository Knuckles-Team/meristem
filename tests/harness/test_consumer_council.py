"""Send the council positions with producer identity in one decision
(MERISTEM-HARNESS-CYCLE-R011.1)."""

from __future__ import annotations

import hashlib
import json

import ports_support as support
import pytest
from support.recorded_engine import RecordedEnginePort, params_digest

from meristem.harness import plan_runtime
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineDecisionRefused

SPEC = "MERISTEM-HARNESS-CYCLE-R011.1"
# spec: MERISTEM-HARNESS-CYCLE-R011.1


def _digest(value: object) -> str:
    canonical = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _positions() -> tuple[plan_runtime.CouncilPosition, ...]:
    return (
        plan_runtime.CouncilPosition(
            council_id="c1",
            topology_revision="3",
            position_id="p1",
            persona_id="pa",
            persona_revision="1",
            option_id="o1",
            claim_digest=hashlib.sha256(b"claim-a").hexdigest(),
            evidence_refs=("e1", "e2"),
            constraint_refs=("cr1",),
        ),
        plan_runtime.CouncilPosition(
            council_id="c1",
            topology_revision="3",
            position_id="p2",
            persona_id="pb",
            persona_revision="2",
            option_id="o2",
            claim_digest=hashlib.sha256(b"claim-b").hexdigest(),
        ),
    )


def _wire() -> list[dict]:
    return [
        {
            "council_id": position.council_id,
            "topology_revision": position.topology_revision,
            "position_id": position.position_id,
            "persona_id": position.persona_id,
            "persona_revision": position.persona_revision,
            "option_id": position.option_id,
            "claim_digest": position.claim_digest,
            "evidence_refs": list(position.evidence_refs),
            "constraint_refs": list(position.constraint_refs),
        }
        for position in _positions()
    ]


@pytest.mark.spec(SPEC)
async def test_positions_carry_producer_identity():
    wire = _wire()
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "council_coordination",
                    "params": {"positions": wire},
                    "answer": {
                        "outcome": "solved",
                        "payload": {"accepted": ["p1"]},
                        "record_digest": "rec-1",
                    },
                }
            ]
        }
    )
    support.register_engine_decision_port(port)

    decision = await plan_runtime.submit_council(
        _positions(), support.PORTS.engine_decision_port(), run_ref="r1"
    )

    # One decision carries every position with its full producer identity.
    assert port.calls == [("council_coordination", params_digest({"positions": wire}))]
    assert decision.accepted == ("p1",)
    assert decision.positions_digest == _digest(wire)
    assert decision.decision_digest == "rec-1"


@pytest.mark.spec(SPEC)
async def test_engine_council_codes_pass_through():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "council_coordination",
                    "params": {"positions": _wire()},
                    "answer": {
                        "outcome": "abstained",
                        "refusal_code": "COUNCIL_NO_QUORUM",
                    },
                }
            ]
        }
    )
    support.register_engine_decision_port(port)

    with pytest.raises(ConsumerRefused, match="COUNCIL_NO_QUORUM"):
        await plan_runtime.submit_council(
            _positions(), support.PORTS.engine_decision_port(), run_ref="r1"
        )


@pytest.mark.spec(SPEC)
async def test_unbound_port_refuses_and_decides_nothing():
    with pytest.raises(EngineDecisionRefused, match="engine_decision_unavailable"):
        await plan_runtime.submit_council(
            _positions(), support.PORTS.engine_decision_port(), run_ref="r1"
        )
