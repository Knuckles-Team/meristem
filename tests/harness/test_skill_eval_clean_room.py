"""Clean-room eval runs (MERISTEM-HARNESS-CYCLE-R099)."""

from __future__ import annotations

import pytest

from meristem.harness.skill_eval_run import SkillEvalCase, digest, run_eval_case
from meristem.runtime.local_workspace import LocalWorkspace


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R099")
async def test_laptop_dependency_ends_the_case_and_attestation_is_sent():
    sent = []
    workspaces = []

    async def submit(method, payload):
        sent.append(dict(payload))

    def fresh():
        workspaces.append(LocalWorkspace())
        return workspaces[-1]

    async def clean(case, router, options, seed):
        code, out, _ = await router.run_command("echo ok")
        return out

    async def dirty(case, router, options, seed):
        await router.run_command("cat /etc/hostname-of-the-laptop")

    case = SkillEvalCase("c1", "real", "p")
    good = await run_eval_case(case, new_workspace=fresh, drive=clean, submit=submit)
    assert good.attestation.host_paths_read == 0 and good.error_code is None
    assert good.attestation.env_digest == digest({})

    bad = await run_eval_case(case, new_workspace=fresh, drive=dirty, submit=submit)
    assert bad.error_code == "LAPTOP_DEPENDENCY"
    assert bad.attestation.host_paths_read == 1
    assert [
        s["request"]["runs"][0]["attestation"]["host_paths_read"] for s in sent
    ] == [0, 1]
    # Each case started its own fresh workspace.
    assert len({id(w) for w in workspaces}) == 2
