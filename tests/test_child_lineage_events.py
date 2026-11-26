"""Each child run a fan-out starts is declared by one ``child_spawned`` run event."""

from __future__ import annotations

import ports_support as support
import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from meristem.graph.live_events import LineageBinding
from meristem.rlm import RLMConfig, run_rlm


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R060")
async def test_fan_out_emits_spawned_child_events(events):
    def root(messages, info):
        returned = sum(
            getattr(part, "part_kind", "") == "tool-return"
            for message in messages
            for part in message.parts
        )
        if returned >= 2:
            return ModelResponse(parts=[TextPart("done")])
        return ModelResponse(
            parts=[ToolCallPart("sub_query", {"question": f"child-{returned}"})]
        )

    def child(messages, info):
        return ModelResponse(parts=[TextPart("child-answer")])

    identities = iter(
        LineageBinding(
            f"child-{n}",
            "run",
            f"c{n}",
            plan_id="plan",
            parent_run_id="root-run",
            parent_node="root",
        )
        for n in range(2)
    )
    result = await run_rlm(
        "root",
        config=RLMConfig(
            model=FunctionModel(root), sub_model=FunctionModel(child), max_depth=2
        ),
        lineage=LineageBinding("root-run", "run", "root", plan_id="plan"),
        child_identity_provider=lambda parent, question: next(identities),
        ports=support.PORTS,
    )
    assert result["ok"]
    spawns = [e for e in events if e["type"] == "child_spawned"]
    assert [e["child_run_id"] for e in spawns] == ["child-0", "child-1"]
    assert {e["run_id"] for e in spawns} == {"root-run"}
    lineage_kinds = {e["type"] for e in events if "lineage" in e}
    assert lineage_kinds <= {"child_spawned", "child_started", "run_started"}
