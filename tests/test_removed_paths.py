"""The removed-name map holds: old names are gone and the new homes work."""

from __future__ import annotations

import ast
import contextlib
import dataclasses
import importlib
import tomllib
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from meristem import host_ports

ROOT = Path(__file__).resolve().parents[1]
MAP = tomllib.loads((Path(__file__).parent / "removed_paths.toml").read_text())
REMOVED = MAP["removed"]
HOSTPORTS_FIELDS = {f.name for f in dataclasses.fields(host_ports.HostPorts)}


def _split(dotted: str) -> tuple[str, str]:
    module, _, name = dotted.rpartition(".")
    return module, name


def _old_is_absent(entry: dict) -> bool:
    module, name = _split(entry["old"])
    if entry["kind"] == "module":
        try:
            importlib.import_module(entry["old"])
        except ModuleNotFoundError:
            return True
        return False
    return not hasattr(importlib.import_module(module), name)


def _new_resolves(entry: dict) -> bool:
    if entry["kind"] == "module":
        importlib.import_module(entry["new"])
        return True
    module, name = _split(entry["new"])
    if module.endswith("HostPorts"):
        return name in HOSTPORTS_FIELDS
    return hasattr(importlib.import_module(module), name)


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R089.1")
@pytest.mark.parametrize("entry", REMOVED, ids=lambda e: e["old"])
def test_every_removed_name_is_absent_and_its_replacement_imports(entry):
    assert _old_is_absent(entry)
    assert _new_resolves(entry)


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R089.1")
def test_no_register_function_remains():
    package = ROOT / "meristem"
    offenders = [
        f"{path.relative_to(ROOT).as_posix()}:{node.name}"
        for path in package.rglob("*.py")
        for node in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("register_")
    ]
    assert offenders == []


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R089.1")
def test_every_hostports_field_has_one_replaced_register_function():
    mapped = {
        entry["new"].rpartition(".")[2]
        for entry in REMOVED
        if entry["old"].rpartition(".")[2].startswith("register_")
    }
    added = {entry["field"] for entry in MAP.get("added", ())}
    assert mapped | added == HOSTPORTS_FIELDS
    assert not mapped & added


@contextlib.asynccontextmanager
async def _keepalive(*_args):
    yield


def _stub_ports(sink: list) -> host_ports.HostPorts:
    """A HostPorts with every field bound to an inert stub; the event sink records."""
    ports = host_ports.HostPorts(
        **{
            name: MagicMock(name=name, return_value=None)
            for name in sorted(HOSTPORTS_FIELDS)
        }
    )
    ports.events = lambda event: sink.append(dict(event))

    async def no_checkpoint(request):
        return host_ports.RunCheckpointSelection(
            run_id=request.run_id, current_run_checkpoint=None, completed_history=None
        )

    ports.run_checkpoints = no_checkpoint
    for name in (
        "keepalive",
        "tool_client",
        "durable_run_events",
        "active_child_input",
        "scaffold_publication",
        "checkpoint_store",
    ):
        setattr(ports, name, _keepalive)
    return ports


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R089.3")
async def test_stub_hostports_runs_a_one_step_graph_without_module_state():
    from pydantic_ai.models.test import TestModel

    from meristem.graph import RunPlan, RunRequest, RunStatus, run

    first: list = []
    second: list = []
    plan = RunPlan(model=TestModel(custom_output_text="done"), steps=("q",))
    one = await run(
        RunRequest(task="t", run_id="r1", ports=_stub_ports(first)), plan=plan
    )
    two = await run(
        RunRequest(task="t", run_id="r2", ports=_stub_ports(second)), plan=plan
    )
    assert one.status is RunStatus.COMPLETED
    assert two.status is RunStatus.COMPLETED
    assert one.output == two.output == "done"
    assert first and second
    assert {e.get("run_id") for e in first if "run_id" in e} <= {"r1"}
    assert {e.get("run_id") for e in second if "run_id" in e} <= {"r2"}
