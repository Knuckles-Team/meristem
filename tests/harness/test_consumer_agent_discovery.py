"""Verified agent facts and the received plan (MERISTEM-SCM-R003.1)."""

from __future__ import annotations

import hashlib
import json

import ports_support as support
import pytest
from support.coding_fakes import Gates, ScriptedEngine, tool_plan

from meristem.graph.executor import iterate_run
from meristem.graph.state import CodingConfig

SPEC = "MERISTEM-SCM-R003.1"
# spec: MERISTEM-SCM-R003.1


async def _run(plan):
    return [e async for e in iterate_run(support.request(task="t"), plan=plan)]


@pytest.mark.spec(SPEC)
async def test_discovery_emits_verified_facts():
    support.reset()
    events: list = []
    support.PORTS.events = events.append
    gates = Gates(
        {
            "claude --version": (0, "claude 2.3.4"),
            "opencode --version": (127, ""),
            "gemini --version": (0, "no version here"),
        }
    )
    engine = ScriptedEngine(
        {
            "assemble": [
                {
                    "outcome": "solved",
                    "record_digest": "r1",
                    "payload": {
                        "transformations": [
                            {"skill": "s", "agent_id": "claude", "artifact_digest": "d"}
                        ]
                    },
                }
            ]
        }
    )
    support.PORTS.gate_runner = lambda run_id: gates
    support.register_engine_decision_port(engine)
    seen: list = []
    await _run(tool_plan("plan_agents", {"skills": ["s"]}, seen, CodingConfig()))

    (call,) = engine.calls
    (fact,) = call[1]["facts"]
    assert fact["agent_id"] == "claude" and fact["revision"] == "2.3.4"
    body = {k: fact[k] for k in ("path", "revision", "exit_code", "probed_at_ms")} | {
        "agent": "claude"
    }
    digest = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert fact["discovery_receipt_digest"] == digest
    assert seen[0]["agents"] == ["claude"]
    assert seen[0]["transformations"] == [{"skill": "s", "agent_id": "claude"}]
    (found,) = [e for e in events if e.get("kind") == "coding_agents_discovered"]
    assert found["receipts"] == [digest]


@pytest.mark.spec(SPEC)
async def test_stale_receipt_reprobes_once_then_surfaces_and_absent_port_refuses():
    support.reset()
    gates = Gates({"claude --version": (0, "1.0.0")})
    stale = {"outcome": "abstained", "refusal_code": "DISCOVERY_RECEIPT_STALE"}
    engine = ScriptedEngine({"assemble": [stale, stale]})
    support.PORTS.gate_runner = lambda run_id: gates
    support.register_engine_decision_port(engine)
    config = CodingConfig(agent_names=("claude",))
    seen: list = []
    await _run(tool_plan("plan_agents", {"skills": []}, seen, config))
    assert len(engine.calls) == 2 and gates.commands == ["claude --version"] * 2
    assert seen == [{"refused": "DISCOVERY_RECEIPT_STALE"}]
    support.PORTS.engine_decision = None
    seen.clear()
    await _run(tool_plan("plan_agents", {"skills": []}, seen, config))
    assert seen == [{"refused": "engine_decision_unavailable"}]
