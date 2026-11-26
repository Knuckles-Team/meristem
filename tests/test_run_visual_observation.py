"""The next tool call carries the committed observation reference, nothing stored."""

from __future__ import annotations

import ports_support as support
import pytest
from pydantic_ai.models.function import FunctionModel

from meristem.graph import (
    DependencyPlan,
    RunPlan,
    RunStatus,
    RunTask,
    ToolStep,
    ToolStepResult,
    run,
)


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R082")
async def test_next_step_uses_committed_reference():
    seen = []

    async def caller(server, tool, arguments, task_id):
        seen.append((tool, dict(arguments)))
        if tool == "resolve_observation":
            return ToolStepResult(result={"observation_ref": "obs://committed/7"})
        return ToolStepResult(result={"ok": True})

    support.PORTS.caller = caller
    plan = RunPlan(
        model=FunctionModel(lambda messages, info: None),
        dependency_plan=DependencyPlan(
            tasks=(
                RunTask(
                    "look",
                    call=ToolStep(
                        "vision", "resolve_observation", {"target": "screen"}
                    ),
                ),
                RunTask(
                    "act",
                    depends_on=("look",),
                    call=ToolStep(
                        "vision",
                        "click",
                        {
                            "observation_ref": {
                                "$ref": "steps.look.output.observation_ref"
                            }
                        },
                    ),
                ),
            ),
            output_task_id="act",
            plan_id="visual",
        ),
    )
    result = await run(support.request(task="t"), plan=plan)
    assert result.status is RunStatus.COMPLETED, result.error
    assert seen[1] == ("click", {"observation_ref": "obs://committed/7"})
