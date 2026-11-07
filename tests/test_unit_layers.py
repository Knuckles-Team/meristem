"""Units import downward only and hold no module-level mutable state."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R088")

PACKAGE = Path(__file__).resolve().parents[1] / "meristem"

#: Lowest first. A unit may import only units that appear before it. ``core``
#: and ``host_ports`` import no other unit; the package root is the top.
LAYERS = (
    "_version",
    "core",
    "host_ports",
    "runtime",
    "harness",
    "graph",
    "rlm",
    "__root__",
)

MUTABLE_CALLS = {
    "list",
    "dict",
    "set",
    "bytearray",
    "deque",
    "defaultdict",
    "OrderedDict",
    "Counter",
    "Lock",
    "RLock",
    "Event",
    "ContextVar",
    "WeakValueDictionary",
    "WeakKeyDictionary",
    "WeakSet",
}


def _unit(path: Path) -> str:
    parts = path.relative_to(PACKAGE).parts
    if len(parts) == 1:
        return (
            "__root__" if parts[0] in ("__init__.py", "__main__.py") else parts[0][:-3]
        )
    return parts[0]


def _modules() -> list[tuple[Path, ast.Module]]:
    return [
        (path, ast.parse(path.read_text(encoding="utf-8")))
        for path in sorted(PACKAGE.rglob("*.py"))
    ]


def _imported_units(path: Path, tree: ast.Module) -> set[str]:
    package = ["meristem", *path.relative_to(PACKAGE).parts[:-1]]
    found: set[str] = set()
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - (node.level - 1)]
                module = ".".join([*base, *([node.module] if node.module else [])])
            else:
                module = node.module or ""
            if module == "meristem":
                names = [f"meristem.{alias.name}" for alias in node.names]
            else:
                names = [module]
        for name in names:
            parts = name.split(".")
            if parts[0] == "meristem" and len(parts) > 1 and parts[1] != "_version":
                found.add(parts[1])
    return found


def _is_mutable(value: ast.expr | None) -> bool:
    if isinstance(value, (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp)):
        return True
    if isinstance(value, ast.Call):
        func = value.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        return name in MUTABLE_CALLS
    return False


def _module_state(tree: ast.Module) -> list[str]:
    found: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and _is_mutable(node.value):
            found.extend(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and _is_mutable(node.value):
            if isinstance(node.target, ast.Name):
                found.append(node.target.id)
    for node in ast.walk(tree):
        if isinstance(node, ast.Global):
            found.extend(node.names)
    return [name for name in found if name != "__all__"]


def test_units_import_downward_and_hold_no_module_state() -> None:
    upward: list[str] = []
    state: list[str] = []
    for path, tree in _modules():
        unit = _unit(path)
        rank = LAYERS.index(unit)
        rel = path.relative_to(PACKAGE).as_posix()
        for target in sorted(_imported_units(path, tree)):
            if target != unit and LAYERS.index(target) >= rank:
                upward.append(f"{rel} imports {target}")
        state.extend(f"{rel}: {name}" for name in _module_state(tree))
    assert upward == []
    assert state == []
