"""The release metadata of 6.0.0 lists the removed-name map and matches the version."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from meristem import __version__

ROOT = Path(__file__).resolve().parents[1]
MAP = tomllib.loads((ROOT / "tests" / "removed_paths.toml").read_text())
CHANGELOG = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def _section(version: str) -> str:
    match = re.search(
        rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)",
        CHANGELOG,
        re.S | re.M,
    )
    assert match, f"no changelog section for {version}"
    return match.group(1)


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R089.2")
@pytest.mark.parametrize("entry", MAP["removed"], ids=lambda e: e["old"])
def test_changelog_lists_every_removed_name_of_the_map(entry):
    section = _section("6.0.0")
    assert f"`{entry['old']}`" in section
    assert f"`{entry['new']}`" in section


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R089.2")
def test_top_changelog_section_matches_the_version():
    headings = re.findall(r"^## \[([^\]]+)\]", CHANGELOG, re.M)
    released = [h for h in headings if h != "Unreleased"]
    assert released[0] == __version__
    assert __version__ == "6.0.0"
    assert (ROOT / "README.md").read_text().count(f"*Version: {__version__}*") == 1
    cfg = (ROOT / ".bumpversion.cfg").read_text()
    assert f"current_version = {__version__}" in cfg
