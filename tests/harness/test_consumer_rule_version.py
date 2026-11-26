"""Read only the active rule version (MERISTEM-HARNESS-CYCLE-R008.1)."""

from __future__ import annotations

import ports_support as support
import pytest
from pydantic_ai.models.test import TestModel
from support.recorded_engine import RecordedEnginePort, params_digest

from meristem.graph import HarnessConfig, RunPlan, create_graph_agent, execute_graph
from meristem.harness import rules, run_lifecycle

SPEC = "MERISTEM-HARNESS-CYCLE-R008.1"
# spec: MERISTEM-HARNESS-CYCLE-R008.1


async def _run_on_start(hook, *, port: RecordedEnginePort | None) -> dict:
    lifecycle = run_lifecycle.RunLifecycle(consumers=("rule_read",))
    lifecycle.add("rule_read", "on_start", hook)
    plan = RunPlan(
        model=TestModel(custom_output_text="done"),
        steps=("q",),
        lifecycle=lifecycle,
        harness=HarnessConfig(consumers=("rule_read",)),
    )
    support.register_run_decision_provider(lambda request: plan)
    if port is not None:
        support.register_engine_decision_port(port)
    graph, config = create_graph_agent(agent_model="m", ports=support.PORTS)
    return await execute_graph(graph, config, "q", run_id="r1", ports=support.PORTS)


@pytest.mark.spec(SPEC)
async def test_trace_records_active_rule_version():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "guardrail_entailment",
                    "params": {"scope": "run"},
                    "answer": {
                        "outcome": "solved",
                        "payload": {
                            "rules": [
                                {"rule_id": "r1", "version": 3, "state": "Active"}
                            ]
                        },
                        "record_digest": "rec-1",
                    },
                }
            ]
        }
    )
    read: dict = {}

    async def read_rules(request, _plan) -> None:
        read["rules"] = await rules.active_rule_versions(
            request, support.PORTS.engine_decision_port(), run_ref="r1"
        )

    result = await _run_on_start(read_rules, port=port)

    assert result["status"] == "completed"
    assert port.calls == [("guardrail_entailment", params_digest({"scope": "run"}))]
    # Each rule id and version enters the run trace.
    assert read["rules"].rules == (("r1", "3"),)
    assert read["rules"].record_digest == "rec-1"


@pytest.mark.spec(SPEC)
async def test_non_active_version_refuses_rule_version_not_active():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "guardrail_entailment",
                    "params": {"scope": "run"},
                    "answer": {
                        "outcome": "solved",
                        "payload": {
                            "rules": [
                                {"rule_id": "r1", "version": 3, "state": "Proposed"}
                            ]
                        },
                    },
                }
            ]
        }
    )

    async def read_rules(request, _plan) -> None:
        await rules.active_rule_versions(
            request, support.PORTS.engine_decision_port(), run_ref="r1"
        )

    result = await _run_on_start(read_rules, port=port)

    assert result["status"] == "failed"
    assert "rule_version_not_active" in (result["error"] or "")


@pytest.mark.spec(SPEC)
async def test_unbound_port_refuses_and_starts_nothing():
    async def read_rules(request, _plan) -> None:
        await rules.active_rule_versions(
            request, support.PORTS.engine_decision_port(), run_ref="r1"
        )

    result = await _run_on_start(read_rules, port=None)

    assert result["status"] == "failed"
    assert "engine_decision_unavailable" in (result["error"] or "")
