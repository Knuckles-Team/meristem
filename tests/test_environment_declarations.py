"""The harness declares every environment name it reads."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import meristem
from meristem import host_ports

PACKAGE = Path(__file__).resolve().parents[1] / "meristem"
ENV_CALLS = {"os.environ.get", "os.getenv", "environ.get", "getenv"}


def _reads() -> tuple[set[str], set[str]]:
    """Literal names read, and files that read the environment without one."""
    names: set[str] = set()
    unnamed: set[str] = set()
    for path in sorted(PACKAGE.rglob("*.py")):
        relative = path.relative_to(PACKAGE.parent).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and ast.unparse(node.func) in ENV_CALLS:
                arg = node.args[0] if node.args else None
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    names.add(arg.value)
                else:
                    unnamed.add(relative)
            elif isinstance(node, ast.Attribute) and ast.unparse(node) == "os.environ":
                unnamed.add(relative)
    return names, unnamed


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R058")
def test_every_read_is_declared():
    names, unnamed = _reads()
    declared = {d.name for d in host_ports.environment_declarations()}
    # The platform directory resolver reads ARBORA_CONFIG_DIR through a constant.
    assert declared - names == {"ARBORA_CONFIG_DIR"}
    assert names <= declared
    # A read without a literal name is a declared forwarder or the resolver.
    allowed = {"meristem/core/paths.py", *host_ports.INHERITED_ENVIRONMENT_FORWARDERS}
    assert unnamed <= allowed


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R058")
def test_declarations_hold_names_only_and_are_sorted():
    declarations = host_ports.environment_declarations("rev1")
    names = [d.name for d in declarations]
    assert names == sorted(names) and len(set(names)) == len(names)
    assert all(d.component == "meristem" and d.default is None for d in declarations)
    assert not any(d.sensitive for d in declarations)
    assert {d.source_revision for d in declarations} == {"rev1"}


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R083")
def test_export_covers_every_environment_read():
    # The package-root export is the one mechanism: it is the same function.
    assert meristem.environment_declarations is host_ports.environment_declarations
    names, _ = _reads()
    exported = {d.name for d in meristem.environment_declarations()}
    assert names <= exported
