"""The run loop refuses a tool call outside the signed allowed subset (R015.1.1, R054)."""

from __future__ import annotations

import ports_support as support
import pytest
from pydantic_ai.models.function import FunctionModel

from meristem.graph import (
    DependencyPlan,
    HarnessConfig,
    RunPlan,
    RunStatus,
    RunTask,
    ToolStep,
    run,
)


def _plan(subset):
    return RunPlan(
        model=FunctionModel(lambda messages, info: pytest.fail("tool-only plan")),
        dependency_plan=DependencyPlan(
            tasks=(RunTask("tool", call=ToolStep("server", "delete_all", {})),),
            output_task_id="tool",
            max_concurrency=1,
            plan_id="subset-plan",
        ),
        harness=HarnessConfig(action_space={"tool_subset": subset}),
    )


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R054")
@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R015.1.1")
async def test_out_of_subset_call_refused(events):
    calls: list[str] = []

    async def caller(server, tool, arguments, task_id):
        calls.append(tool)
        from meristem.host_ports import ToolStepResult

        return ToolStepResult(result="done")

    support.register_tool_caller(caller)
    refused = await run(support.request(task="t"), plan=_plan(["read_file"]))
    assert refused.status is RunStatus.FAILED
    assert "TOOL_NOT_ALLOWED" in (refused.error or "") and calls == []
    failed = next(e for e in events if e["type"] == "task_failed")
    assert failed["task_id"] == "tool"
    allowed = await run(support.request(task="t"), plan=_plan(["delete_all"]))
    assert allowed.status is RunStatus.COMPLETED and calls == ["delete_all"]
