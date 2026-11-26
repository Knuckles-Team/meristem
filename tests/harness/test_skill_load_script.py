"""Skill load script (MERISTEM-HARNESS-CYCLE-R096)."""

from __future__ import annotations

import pytest

from meristem.harness.skill_loader import SkillLoader
from meristem.runtime.local_workspace import LocalWorkspace


class Source:
    def __init__(self, script):
        self.script = script

    async def metadata(self, ref):
        return ref, "d"

    async def body(self, ref):
        return "head\n{{load_script_output}}\ntail"

    async def reference(self, ref, path):
        return ""

    async def load_script(self, ref):
        return self.script


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R096")
async def test_output_replaces_placeholder_and_failure_keeps_it():
    workspace = LocalWorkspace()
    await workspace.start()
    try:
        ok = SkillLoader(Source("echo hello"), lambda e: None, workspace=workspace)
        assert (await ok.stage2("a")).text == "head\nhello\n\ntail"

        traces = []
        bad = SkillLoader(
            Source("exit 3"),
            lambda e: None,
            workspace=workspace,
            trace=lambda kind, payload: traces.append((kind, dict(payload))),
        )
        assert "{{load_script_output}}" in (await bad.stage2("a")).text
        assert traces == [
            ("SKILL_LOAD_SCRIPT_FAILED", {"skill_ref": "a", "exit_code": 3})
        ]

        big = SkillLoader(
            Source("head -c 20000 /dev/zero | tr '\\0' x"),
            lambda e: None,
            workspace=workspace,
        )
        assert (await big.stage2("a")).text.count("x") == 8192
    finally:
        await workspace.stop()
