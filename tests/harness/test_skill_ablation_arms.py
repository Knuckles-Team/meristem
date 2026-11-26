"""Ablation arms with a blind label (MERISTEM-HARNESS-CYCLE-R100)."""

from __future__ import annotations

import pytest

from meristem.harness.skill_eval_run import (
    AblationArm,
    SkillEvalCase,
    apply_arm,
    run_eval_case,
)
from meristem.harness.skill_loader import SkillLoader
from meristem.runtime.local_workspace import LocalWorkspace


class Source:
    async def metadata(self, ref):
        return ref, "d"

    async def body(self, ref):
        return "# Intro\nkeep\n# Extra\ndrop"

    async def reference(self, ref, path):
        return "r"

    async def load_script(self, ref):
        return None


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R100")
async def test_arms_remove_what_they_name_and_hide_the_label():
    seeds = []
    results = []

    async def submit(method, payload):
        results.append(dict(payload))

    async def drive(case, router, options, seed):
        seeds.append(seed)
        loader = SkillLoader(Source(), lambda e: None, **options)
        listed = [s.skill_ref for s in await loader.stage1(["a", "b"])]
        return listed, (await loader.stage2("a")).text

    case = SkillEvalCase("c1", "real", "p")
    arms = [
        None,
        AblationArm("Without", "a"),
        AblationArm("WithoutPart", "a", "Extra"),
    ]
    out = []
    for arm in arms:
        out.append(
            await run_eval_case(
                case,
                new_workspace=LocalWorkspace,
                drive=drive,
                submit=submit,
                arm=arm,
                seed=7,
            )
        )
    assert seeds == [7, 7, 7]
    assert len({r.blind_id for r in out}) == 3
    for payload in results:
        text = repr(payload)
        assert "Without" not in text and "WithoutPart" not in text

    # The arms remove exactly what they name.
    loader = apply_arm(
        SkillLoader(Source(), lambda e: None), AblationArm("Without", "a")
    )
    assert [s.skill_ref for s in await loader.stage1(["a", "b"])] == ["b"]
    part = apply_arm(
        SkillLoader(Source(), lambda e: None), AblationArm("WithoutPart", "a", "Extra")
    )
    assert (await part.stage2("a")).text == "# Intro\nkeep"
