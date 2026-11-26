"""Code step (MERISTEM-HARNESS-CYCLE-R097)."""

from __future__ import annotations

import pytest

from meristem.harness.plan_runtime import run_code_step
from meristem.runtime.local_workspace import LocalWorkspace


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R097")
async def test_only_the_return_value_reaches_context_and_large_return_fails():
    workspace = LocalWorkspace()
    await workspace.start()
    try:
        script = "echo intermediate >&2; echo done"
        step = {"mode": "code", "script": script}
        result = await run_code_step(step, workspace)
        assert (result.status, result.value) == ("completed", "done\n")
        assert "intermediate" not in result.value

        big = {"mode": "code", "script": "head -c 100 /dev/zero | tr '\\0' x"}
        failed = await run_code_step(big, workspace, return_max_bytes=50)
        assert (failed.status, failed.code) == ("failed", "CODE_STEP_RETURN_TOO_LARGE")
        assert failed.value == ""
    finally:
        await workspace.stop()
