"""Flag and friction tools (MERISTEM-HARNESS-CYCLE-R093)."""

from __future__ import annotations

import ports_support as support
import pytest
from pydantic_ai.models.test import TestModel

from meristem.graph.executor import resolve_plan
from meristem.graph.state import RunPlan
from meristem.harness.knowledge_tools import (
    FlagToolUnbound,
    build_knowledge_tools,
    require_knowledge_tools,
)


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R093")
async def test_both_tools_bound_under_allowed_tools_and_unbound_plan_fails():
    support.reset()

    class Native:
        def __init__(self, names):
            self.names = names

        def available_tools(self):
            return self.names

    support.register_native_toolset_provider(
        Native(["only_this", "knowledge_flag", "skills_friction_report"])
    )
    support.register_run_decision_provider(
        lambda request: RunPlan(model=TestModel(), tool_names=("only_this",))
    )
    request = support.request("task", allowed_tools=["only_this"])
    plan = await resolve_plan(request, {})
    assert plan.tool_names == ("only_this", "knowledge_flag", "skills_friction_report")

    # The default plan binds both tools as well.
    support.PORTS.decide = None
    plan = await resolve_plan(request, {"agent_model": "m"})
    assert {"knowledge_flag", "skills_friction_report"} <= set(plan.tool_names)

    # A host that serves only one of the tools fails the plan at plan time.
    support.register_native_toolset_provider(Native(["knowledge_flag"]))
    with pytest.raises(FlagToolUnbound):
        await resolve_plan(request, {"agent_model": "m"})

    # A tool set that lacks either tool is refused with the closed code.
    with pytest.raises(FlagToolUnbound) as refused:
        require_knowledge_tools(("knowledge_flag",))
    assert refused.value.code == "FLAG_TOOL_UNBOUND"

    # The tools add the run id and the current trace node id to the call.
    seen: list[tuple[str, dict]] = []

    async def call(operation, arguments):
        seen.append((operation, dict(arguments)))
        return {"flag_id": "f1"}

    tools = build_knowledge_tools(
        call, run_id="r1", trace_node_id=lambda: "n7", clock_ms=lambda: 5
    )
    assert await tools["knowledge_flag"]("skill:x@1", "wrong", "saw y", "fix") == "f1"
    friction = await tools["skills_friction_report"](
        "skill:x@1", "1", "ran it", "fast", "slow"
    )
    assert friction == "r1:friction:1"
    op, body = seen[0]
    assert op == "knowledge.flag.raise" and body["op"] == "raise"
    assert body["run_id"] == "r1" and body["trace_node_id"] == "n7"
    assert body["suggested_correction"] == "fix" and "correction" not in body
    assert (body["knowledge_kind"], body["flag_kind"], body["at_ms"]) == (
        "skill",
        "wrong",
        5,
    )
    assert (body["target_ref"], body["what_observed"]) == ("skill:x@1", "saw y")
    assert seen[1][0] == "skills.friction.report" and seen[1][1]["op"] == "report"
    report = seen[1][1]["report"]
    assert report["skill_ref"] == "skill:x@1" and "proposed_fix" not in report
    assert (report["tried"], report["expected"], report["happened"]) == (
        "ran it",
        "fast",
        "slow",
    )
