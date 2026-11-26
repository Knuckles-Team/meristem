"""Verify the immutable command file against its sealed record
(MERISTEM-HARNESS-CYCLE-R005.2)."""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

import ports_support as support
import pytest
from pydantic_ai.models.test import TestModel
from support.recorded_engine import RecordedEnginePort, params_digest

from meristem.graph import HarnessConfig, RunPlan, create_graph_agent, execute_graph
from meristem.harness import command_file, run_lifecycle

SPEC = "MERISTEM-HARNESS-CYCLE-R005.2"
# spec: MERISTEM-HARNESS-CYCLE-R005.2

CONFIG = {"retrieval": "kg"}
MODE = {"mode": "strict"}


def _digest(value: object) -> str:
    canonical = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


CONFIG_DIGEST = _digest(CONFIG)
MODE_DIGEST = _digest(MODE)


async def _run_with_document(hook, *, port: RecordedEnginePort | None, document: str):
    """One run whose on_start consumer reads a command file the test writes."""
    lifecycle = run_lifecycle.RunLifecycle(consumers=("command_file",))

    async def writing(_request, _plan) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "command.json"
            path.write_text(document)
            await hook(path, _plan)

    lifecycle.add("command_file", "on_start", writing)
    plan = RunPlan(
        model=TestModel(custom_output_text="done"),
        steps=("q",),
        lifecycle=lifecycle,
        harness=HarnessConfig(consumers=("command_file",)),
    )
    support.register_run_decision_provider(lambda request: plan)
    if port is not None:
        support.register_engine_decision_port(port)
    graph, config = create_graph_agent(agent_model="m", ports=support.PORTS)
    return await execute_graph(graph, config, "q", run_id="r1", ports=support.PORTS)


@pytest.mark.spec(SPEC)
async def test_verified_file_returns_sealed_digests():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "retrieval_plan",
                    "params": {
                        "retrieval.command_config_digest": CONFIG_DIGEST,
                        "retrieval.inference_mode_digest": MODE_DIGEST,
                    },
                    "answer": {
                        "outcome": "solved",
                        "payload": {
                            "retrieval.command_config_digest": CONFIG_DIGEST,
                            "retrieval.inference_mode_digest": MODE_DIGEST,
                        },
                        "record_digest": "rec-1",
                    },
                }
            ]
        }
    )
    loaded: dict = {}
    document = json.dumps({"command_config": CONFIG, "inference_mode": MODE})

    async def load(path, _plan) -> None:
        loaded["file"] = await command_file.load_command_file(
            path, support.PORTS.engine_decision_port(), run_ref="r1"
        )

    result = await _run_with_document(load, port=port, document=document)

    assert result["status"] == "completed"
    assert port.calls == [
        (
            "retrieval_plan",
            params_digest(
                {
                    "retrieval.command_config_digest": CONFIG_DIGEST,
                    "retrieval.inference_mode_digest": MODE_DIGEST,
                }
            ),
        )
    ]
    command = loaded["file"]
    assert command.config_digest == CONFIG_DIGEST
    assert command.mode_digest == MODE_DIGEST
    assert command.record_digest == "rec-1"


@pytest.mark.spec(SPEC)
async def test_mode_digest_mismatch_refuses_load():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "retrieval_plan",
                    "params": {
                        "retrieval.command_config_digest": CONFIG_DIGEST,
                        "retrieval.inference_mode_digest": MODE_DIGEST,
                    },
                    "answer": {
                        "outcome": "solved",
                        "payload": {
                            "retrieval.command_config_digest": CONFIG_DIGEST,
                            "retrieval.inference_mode_digest": "0" * 64,
                        },
                    },
                }
            ]
        }
    )
    document = json.dumps({"command_config": CONFIG, "inference_mode": MODE})

    async def load(path, _plan) -> None:
        await command_file.load_command_file(
            path, support.PORTS.engine_decision_port(), run_ref="r1"
        )

    result = await _run_with_document(load, port=port, document=document)

    assert result["status"] == "failed"
    assert "command_mode_digest_mismatch" in (result["error"] or "")


@pytest.mark.spec(SPEC)
async def test_bad_schema_refuses_command_file_schema_invalid():
    port = RecordedEnginePort({"answers": []})
    document = "not a command file"

    async def load(path, _plan) -> None:
        await command_file.load_command_file(
            path, support.PORTS.engine_decision_port(), run_ref="r1"
        )

    result = await _run_with_document(load, port=port, document=document)

    assert result["status"] == "failed"
    assert "command_file_schema_invalid" in (result["error"] or "")
    # The schema is validated before any engine question.
    assert port.calls == []


@pytest.mark.spec(SPEC)
async def test_unbound_port_refuses_and_starts_nothing():
    document = json.dumps({"command_config": CONFIG, "inference_mode": MODE})

    async def load(path, _plan) -> None:
        await command_file.load_command_file(
            path, support.PORTS.engine_decision_port(), run_ref="r1"
        )

    result = await _run_with_document(load, port=None, document=document)

    assert result["status"] == "failed"
    assert "engine_decision_unavailable" in (result["error"] or "")
