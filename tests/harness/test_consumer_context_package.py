"""Apply the engine's chosen context package with its citations
(MERISTEM-HARNESS-CYCLE-R017.2)."""

from __future__ import annotations

import ports_support as support
import pytest
from support.recorded_engine import RecordedEnginePort, params_digest

from meristem.harness import plan_runtime
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineDecisionRefused

SPEC = "MERISTEM-HARNESS-CYCLE-R017.2"
# spec: MERISTEM-HARNESS-CYCLE-R017.2

UNITS = [
    {"id": "u1", "source": "kg", "span": "s-1", "revision": "7", "content": "alpha"},
    {"id": "u2", "source": "docs", "span": "s-2", "revision": "1", "content": "beta"},
]
BUDGET = 100
PARAMS = {"evidence_units": UNITS, "budget_tokens": BUDGET}


@pytest.mark.spec(SPEC)
async def test_chosen_ids_and_citations_applied():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "context_package",
                    "params": PARAMS,
                    "answer": {
                        "outcome": "solved",
                        "payload": {
                            "chosen": [{"id": "u1", "resolution": "summarized"}]
                        },
                        "record_digest": "rec-1",
                    },
                }
            ]
        }
    )
    support.register_engine_decision_port(port)

    package = await plan_runtime.compile_context_package(
        UNITS, BUDGET, support.PORTS.engine_decision_port(), run_ref="r1"
    )

    assert port.calls == [("context_package", params_digest(PARAMS))]
    (unit,) = package.chosen
    # Applied exactly as received, with the sent unit's citation kept.
    assert unit.unit_id == "u1"
    assert unit.resolution == "summarized"
    assert (unit.source, unit.span, unit.revision) == ("kg", "s-1", "7")
    assert package.record_digest == "rec-1"


@pytest.mark.spec(SPEC)
async def test_chosen_id_outside_the_sent_units_refuses():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "context_package",
                    "params": PARAMS,
                    "answer": {
                        "outcome": "solved",
                        "payload": {
                            "chosen": [{"id": "u9", "resolution": "summarized"}]
                        },
                    },
                }
            ]
        }
    )
    support.register_engine_decision_port(port)

    with pytest.raises(ConsumerRefused, match="package_unit_unknown"):
        await plan_runtime.compile_context_package(
            UNITS, BUDGET, support.PORTS.engine_decision_port(), run_ref="r1"
        )


@pytest.mark.spec(SPEC)
async def test_caller_owned_cache_replays_the_answer():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "context_package",
                    "params": PARAMS,
                    "answer": {
                        "outcome": "solved",
                        "payload": {"chosen": [{"id": "u2", "resolution": "full"}]},
                        "record_digest": "rec-1",
                    },
                },
                {
                    "method": "context_package",
                    "params": {"evidence_units": UNITS, "budget_tokens": BUDGET + 10},
                    "answer": {
                        "outcome": "solved",
                        "payload": {"chosen": []},
                        "record_digest": "rec-2",
                    },
                },
            ]
        }
    )
    support.register_engine_decision_port(port)
    cache: dict = {}

    first = await plan_runtime.compile_context_package(
        UNITS, BUDGET, support.PORTS.engine_decision_port(), run_ref="r1", cache=cache
    )
    replayed = await plan_runtime.compile_context_package(
        UNITS, BUDGET, support.PORTS.engine_decision_port(), run_ref="r1", cache=cache
    )
    changed = await plan_runtime.compile_context_package(
        UNITS,
        BUDGET + 10,
        support.PORTS.engine_decision_port(),
        run_ref="r1",
        cache=cache,
    )

    # The cache is caller-owned: same units and budget replay without a call;
    # a changed budget makes a new call and a new cache entry.
    assert replayed is first
    assert changed is not first
    assert len(port.calls) == 2
    assert len(cache) == 2
    assert changed.record_digest == "rec-2"


@pytest.mark.spec(SPEC)
async def test_unbound_port_refuses_and_decides_nothing():
    with pytest.raises(EngineDecisionRefused, match="engine_decision_unavailable"):
        await plan_runtime.compile_context_package(
            UNITS, BUDGET, support.PORTS.engine_decision_port(), run_ref="r1"
        )
