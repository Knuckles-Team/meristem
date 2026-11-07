"""MERISTEM-HARNESS-CYCLE-R090 (PIPE-GATES-R086.8): this repository runs the shared state-machine census.

The census is adopted in mode ``staged`` (findings are printed, the hook exits
0) until the last state-machine row of this repository is verified; the hook
then moves to ``blocking``. The shared hook itself is tested in pipelines.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _pipelines_block() -> str:
    """The `.config/pre-commit.yaml` repo block of the shared pipelines hooks."""
    text = (ROOT / ".config/pre-commit.yaml").read_text(encoding="utf-8")
    start = text.index("- repo: https://github.com/Knuckles-Team/pipelines\n")
    end = text.find("\n- repo:", start + 1)
    return text[start : end if end != -1 else len(text)]


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R090")
def test_census_exits_zero() -> None:
    policy = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "tool"
    ]["pipelines_hooks"]
    table = policy["state_machine"]
    assert table["mode"] == "staged"
    assert table["paths"] and all((ROOT / path).is_dir() for path in table["paths"])
    assert set(table["languages"]) <= {"python", "rust"}
    block = _pipelines_block()
    assert "  rev: main\n" in block
    assert "- id: state-machine-shape\n    stages: [pre-commit, pre-push]\n" in block
    assert "- id: state-machine-census\n    stages: [manual]\n" in block
