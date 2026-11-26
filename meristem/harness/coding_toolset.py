"""The coding toolset: the run loop's one caller of the changeset cycle (MERISTEM-SCM-R010).

Appended by ``build_agent`` when ``HarnessConfig.coding`` is set. Each tool
applies a received value or a received engine answer; the host binds the
changeset submitter and the gate runner on ``HostPorts``. An unbound one
returns ``submitter_unavailable`` or ``sandbox_unavailable``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import asdict
from typing import Any

from pydantic_ai.toolsets import FunctionToolset

from meristem.graph.state import CodingConfig
from meristem.harness import (
    agent_discovery,
    correction_binding,
    edit_round,
    preplan,
    task_run,
)
from meristem.harness.changeset_loop import run_changeset_cycle
from meristem.harness.edit_engine import Edit
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import HostPorts
from meristem.runtime.local_workspace import LocalWorkspace
from meristem.runtime.workspace import DevWorkspace

_EDIT_KEYS = frozenset({"path", "search", "replace"})


def parse_edits(raw: Sequence[Mapping[str, Any]]) -> tuple[Edit, ...] | None:
    """Closed edit objects; ``None`` when any entry is malformed."""
    edits: list[Edit] = []
    for item in raw:
        if (
            not isinstance(item, Mapping)
            or not _EDIT_KEYS <= set(item)
            or not set(item) <= _EDIT_KEYS | {"fmt"}
            or not all(isinstance(v, str) for v in item.values())
        ):
            return None
        edits.append(Edit(**item))
    return tuple(edits)


class CodingToolset(FunctionToolset[Any]):
    """The five coding tools over one disposable workspace of this run."""

    def __init__(self, config: CodingConfig, *, run_id: str, ports: HostPorts) -> None:
        super().__init__()
        self._config = config
        self._run_id = run_id
        self._ports = ports
        self._workspace: DevWorkspace | None = None
        self._preplan_receipt: preplan.PrePlanTestReceipt | None = None
        tools: list[Any] = [
            self.plan_agents,
            self.run_preplan,
            self.run_edit_round,
            self.submit_changeset,
            self.bind_correction,
        ]
        if config.task_run is not None:
            tools.append(self.run_task)
        for tool in tools:
            self.add_function(tool, name=tool.__name__)

    async def __aexit__(self, *args: Any) -> bool | None:
        if self._workspace is not None:
            await self._workspace.stop()
            self._workspace = None
        return await super().__aexit__(*args)

    def _bound(self) -> tuple[Any, Any]:
        submitter = self._ports.changeset_submitter
        runner = self._ports.gate_runner
        return (
            submitter(self._run_id) if submitter is not None else None,
            runner(self._run_id) if runner is not None else None,
        )

    def _space(self) -> DevWorkspace:
        if self._workspace is None:
            self._workspace = DevWorkspace(LocalWorkspace(), run_id=self._run_id)
        return self._workspace

    async def _guard(self, work: Awaitable[Any]) -> Any:
        """A consumer refusal reaches the model as a step observation."""
        try:
            return await work
        except ConsumerRefused as refusal:
            return {"refused": refusal.code}

    async def plan_agents(self, skills: list[str]) -> Any:
        """Probe the configured agents and return the engine's coding-agent plan."""
        _, runner = self._bound()
        if runner is None:
            return {"refused": "sandbox_unavailable"}
        workspace = self._space()
        await workspace.start()

        async def probe() -> Any:
            return await agent_discovery.discover_agents(
                self._config.agent_names,
                runner,
                workspace,
                capabilities=self._config.agent_capabilities,
            )

        plan = await self._guard(
            agent_discovery.plan_coding_agents(
                self._ports.engine_decision_port(),
                probe,
                skills,
                run_ref=self._run_id,
                emit=self._ports.emit_run_event,
            )
        )
        if isinstance(plan, dict):
            return plan
        return {
            "agents": [f.agent_id for f in plan.facts],
            "transformations": [
                {"skill": t.skill, "agent_id": t.agent_id} for t in plan.transformations
            ],
        }

    async def run_preplan(self) -> Any:
        """Run the pre-plan tests and commit their receipt with the program."""
        submitter, runner = self._bound()
        if submitter is None:
            return {"refused": "submitter_unavailable"}
        if runner is None:
            return {"refused": "sandbox_unavailable"}
        workspace = self._space()
        await workspace.start()
        head = await submitter.read_head()
        await submitter.materialize(head, workspace)
        port = self._ports.engine_decision_port()
        receipt = await self._guard(
            preplan.run_preplan(
                self._config.preplan_command,
                self._config.preplan_artifacts,
                runner,
                workspace,
                port,
                run_id=self._run_id,
                repository_id=self._run_id,
                revision=head,
            )
        )
        if isinstance(receipt, dict):
            return receipt
        self._preplan_receipt = receipt
        return await self._guard(
            preplan.commit_compiled_program(
                receipt, port, run_ref=self._run_id, emit=self._ports.emit_run_event
            )
        )

    async def run_edit_round(
        self, edits: list[dict[str, str]], prediction: dict[str, Any]
    ) -> str:
        """Apply edits under a declared prediction; keep them only if verified."""
        submitter, runner = self._bound()
        if submitter is None:
            return "submitter_unavailable"
        if runner is None:
            return "sandbox_unavailable"
        parsed = parse_edits(edits)
        if parsed is None:
            return "invalid_edit"
        result = await self._guard(
            edit_round.run_edit_round(
                parsed,
                prediction,
                self._config.tasks,
                runner,
                submitter,
                self._space(),
                self._ports.engine_decision_port(),
                base_head=await submitter.read_head(),
                run_ref=self._run_id,
                emit=self._ports.emit_run_event,
            )
        )
        return result if isinstance(result, str) else f"refused: {result['refused']}"

    async def submit_changeset(
        self, edits: list[dict[str, str]], fast_gates: list[str]
    ) -> str:
        """Run the fast gates and submit one changeset; rebase once per conflict."""
        submitter, runner = self._bound()
        parsed = parse_edits(edits)
        if parsed is None:
            return "invalid_edit"
        result = await run_changeset_cycle(
            self._space(),
            parsed,
            base_head=await submitter.read_head() if submitter is not None else "",
            run_ref=self._run_id,
            fast_gates=tuple(fast_gates),
            submitter=submitter,
            gate_runner=runner,
            ports=self._ports,
        )
        return result.changeset_id or result.reason

    async def run_task(self, edits: list[dict[str, str]], fast_gates: list[str]) -> Any:
        """Run the one received task: one changeset cycle, result with evidence."""
        context = self._config.task_run
        parsed = parse_edits(edits)
        if parsed is None or context is None:
            return {"status": "failed", "failure": {"code": "invalid_edit"}}
        submitter, runner = self._bound()
        outcome = await task_run.run_task_run(
            context,
            parsed,
            tuple(fast_gates),
            submitter=submitter,
            gate_runner=runner,
            workspace=self._space(),
            ports=self._ports,
        )
        return asdict(outcome)

    async def bind_correction(
        self,
        trace_id: str,
        failure_point: str,
        fix_id: str,
        changeset_digest: str,
        test_id: str,
        before_head: str,
        after_head: str,
    ) -> Any:
        """Run the regression test on both revisions and ask for admission."""
        submitter, runner = self._bound()
        if submitter is None:
            return {"refused": "submitter_unavailable"}
        if runner is None:
            return {"refused": "sandbox_unavailable"}
        admitted = await self._guard(
            correction_binding.bind_correction(
                run_id=self._run_id,
                trace_id=trace_id,
                failure_point=failure_point,
                fix_id=fix_id,
                changeset_digest=changeset_digest,
                test_id=test_id,
                command=self._config.regression_command,
                before_head=before_head,
                after_head=after_head,
                runner=runner,
                submitter=submitter,
                workspace=self._space(),
                port=self._ports.engine_decision_port(),
                emit=self._ports.emit_run_event,
            )
        )
        return admitted if isinstance(admitted, dict) else {"admitted": admitted}
