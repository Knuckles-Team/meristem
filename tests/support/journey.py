"""Run journeys: one received plan driven through ``meristem.graph.execute_graph``."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import ports_support as support
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from meristem.graph import (
    DependencyPlan,
    HarnessConfig,
    RunPlan,
    RunTask,
    ToolStep,
    execute_graph,
)
from meristem.host_ports import ToolStepResult


def text_plan(consumers, prompts=None, **harness) -> RunPlan:
    """A one-step text plan whose model records the prompt it receives."""

    def respond(messages, info):
        if prompts is not None:
            prompts.append(messages[-1].parts[-1].content)
        return ModelResponse(parts=[TextPart("reply")])

    return RunPlan(
        model=FunctionModel(respond),
        steps=("question",),
        harness=HarnessConfig(consumers=tuple(consumers), **harness),
    )


def tool_plan(consumers, tools, **harness) -> RunPlan:
    """A chain of tool tasks ``t0 -> t1 ...``; each tool is ``(name, arguments)``."""
    tasks = tuple(
        RunTask(
            f"t{i}",
            depends_on=(f"t{i - 1}",) if i else (),
            call=ToolStep("srv", name, dict(arguments)),
        )
        for i, (name, arguments) in enumerate(tools)
    )

    async def never(messages, info):
        return ModelResponse(parts=[TextPart("unexpected")])

    return RunPlan(
        model=FunctionModel(never),
        dependency_plan=DependencyPlan(
            tasks=tasks, output_task_id=tasks[-1].task_id, plan_id="sha256:plan"
        ),
        harness=HarnessConfig(consumers=tuple(consumers), **harness),
    )


def bind_caller() -> list[str]:
    """Bind a tool caller that records each tool it runs."""
    called: list[str] = []

    async def caller(server, tool, arguments, task_id):
        called.append(tool)
        return ToolStepResult(result={"tool": tool})

    support.PORTS.caller = caller
    return called


async def execute(plan: RunPlan, metadata: Mapping[str, Any] | None = None) -> dict:
    """Run ``plan`` through ``execute_graph`` with this test's bound ports."""
    support.register_run_decision_provider(lambda _request: plan)
    result = await execute_graph(
        None, {}, "q", run_id="r1", metadata=metadata, ports=support.PORTS
    )
    # The run-state machine: a completed run never carries an error.
    assert not (result["status"] == "completed" and result["error"])
    return result
