"""Fakes for coding-toolset run tests: a scripted engine port and a scripted gate runner."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from meristem import host_ports as hp
from meristem.graph import RunPlan
from meristem.graph.state import CodingConfig, HarnessConfig
from meristem.runtime.events import CmdOutputObservation


class ScriptedEngine:
    """Serves queued answers per method and records every call."""

    def __init__(self, answers: Mapping[str, list[dict[str, Any]]]) -> None:
        self.answers = {k: list(v) for k, v in answers.items()}
        self.calls: list[tuple[str, Any]] = []

    def _next(self, method: str, arg: Any) -> hp.EngineAnswer:
        self.calls.append((method, arg))
        return hp.EngineAnswer(**self.answers[method].pop(0))

    async def decide(self, kind, params, *, run_ref):
        return self._next("decide", (kind, params))

    async def assemble(self, request, *, run_ref):
        return self._next("assemble", request)

    async def log(self, op, body, *, run_ref):
        return self._next("log", op)


class Gates:
    """Gate runner whose command results are scripted by command text."""

    def __init__(self, results: Mapping[str, tuple[int, str]], files=None) -> None:
        self.results = dict(results)
        self.commands: list[str] = []
        self.files = dict(files or {})
        self.applied: list[Any] = []

    async def run_gate(self, workspace, command):
        self.commands.append(command)
        code, out = self.results[command]
        return CmdOutputObservation(exit_code=code, stdout=out)

    async def apply_edits(self, workspace, edits):
        self.applied.append(edits)
        return True

    async def read_files(self, workspace, paths):
        return tuple((p, self.files.get(p, "")) for p in paths)


class Heads:
    """Submitter fake that records the heads it materialized."""

    def __init__(self) -> None:
        self.heads: list[str] = []

    async def read_head(self):
        return "head-0"

    async def materialize(self, head, workspace):
        self.heads.append(head)

    async def submit(self, pending):  # pragma: no cover - not reached
        raise AssertionError


def tool_plan(tool: str, args: dict, seen: list, config: CodingConfig) -> RunPlan:
    """A plan whose model calls one coding tool, then records its result."""

    def respond(messages, info):
        last = messages[-1].parts[-1]
        if getattr(last, "part_kind", "") == "tool-return":
            seen.append(last.content)
            return ModelResponse(parts=[TextPart("done")])
        return ModelResponse(parts=[ToolCallPart(tool, json.dumps(args))])

    return RunPlan(
        model=FunctionModel(respond),
        steps=("code",),
        harness=HarnessConfig(coding=config),
    )


class LocalSource:
    """Source and gate runner over a real local workspace root (a control-flow double)."""

    def __init__(self, outcomes) -> None:
        self.outcomes = iter(outcomes)
        self.submitted: list[Any] = []

    async def materialize(self, head, workspace):
        (workspace.backend.root / "a.txt").write_text("before\n")

    async def read_head(self):
        return "new-head"

    async def submit(self, pending):
        self.submitted.append(pending)
        return next(self.outcomes)

    async def run_gate(self, workspace, command):
        from meristem.runtime.events import CmdRunAction

        return await workspace.act(CmdRunAction(command=command))

    async def apply_edits(self, workspace, edits):
        from meristem.harness.edit_engine import apply_edits

        root = workspace.backend.root
        if not apply_edits(list(edits), root=root, dry_run=True).ok:
            return False
        return apply_edits(list(edits), root=root).ok

    async def read_files(self, workspace, paths):
        return tuple(
            (p, (workspace.backend.root / p).read_text(encoding="utf-8")) for p in paths
        )


def local_workspace(root):
    from meristem.runtime.local_workspace import LocalWorkspace
    from meristem.runtime.workspace import DevWorkspace

    return DevWorkspace(LocalWorkspace(root), run_id="run")
