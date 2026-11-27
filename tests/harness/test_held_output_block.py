"""A completed run emits the held-output block of its exact output (R101)."""

from __future__ import annotations

import hashlib
import json

import ports_support as support
import pytest
from pydantic_ai.models.test import TestModel

from meristem.graph import HarnessConfig, HeldOutputConfig, RunPlan
from meristem.graph.executor import iterate_run
from meristem.harness.held_item import held_output_block

CONFIG = HeldOutputConfig(
    item_ref="item-1",
    engine_decision_ref="decision-1",
    checklist=(("c1", "Is the answer grounded?"),),
    verifier="verifier-a",
    compiler="compiler-b",
    verification_ref="verification-1",
)


async def _terminal(held: HeldOutputConfig | None, text: str = "done") -> dict:
    plan = RunPlan(
        model=TestModel(custom_output_text=text),
        steps=("q",),
        harness=HarnessConfig(held_output=held),
    )
    events = [e async for e in iterate_run(support.request(task="t"), plan=plan)]
    return events[-1]


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R101")
async def test_completed_run_emits_the_held_output_block():
    terminal = await _terminal(CONFIG, "done")
    assert terminal["type"] == "graph_complete"
    block = terminal["result"]["held_output"]
    assert block["content_digest"] == hashlib.sha256(b"done").hexdigest()
    assert block["item_ref"] == "item-1"
    assert block["engine_decision_ref"] == "decision-1"
    assert block["checklist"] == [{"id": "c1", "question": "Is the answer grounded?"}]
    assert block["verification"] == {
        "verifier": "verifier-a",
        "compiler": "compiler-b",
        "verification_ref": "verification-1",
    }

    # The host may digest its own encoding of the exact output; the block follows it.
    def encode(text: str) -> bytes:
        return json.dumps([{"text": text}], sort_keys=True).encode()

    held = held_output_block(
        "done",
        HeldOutputConfig("i", "d", (("c", "q"),), encode=encode),
    )
    assert held["content_digest"] == hashlib.sha256(encode("done")).hexdigest()
    assert "verification" not in held


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R101")
async def test_no_block_without_config_or_completion():
    assert "held_output" not in (await _terminal(None))["result"]
    failing = RunPlan(
        model=TestModel(call_tools=["missing"]),
        steps=("q",),
        harness=HarnessConfig(held_output=CONFIG),
        tool_names=("missing",),
    )
    events = [e async for e in iterate_run(support.request(task="t"), plan=failing)]
    assert events[-1]["result"]["status"] != "completed"
    assert "held_output" not in events[-1]["result"]
