"""The removed-name map holds: old names are gone and the new homes work."""

from __future__ import annotations

import ast
import dataclasses
import importlib
import tomllib
from pathlib import Path

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
