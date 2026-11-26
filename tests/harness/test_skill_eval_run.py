"""Eval case replay (MERISTEM-HARNESS-CYCLE-R098)."""

from __future__ import annotations

import pytest

from meristem.harness.skill_eval_run import SkillEvalCase, digest, run_eval_case
from meristem.runtime.local_workspace import LocalWorkspace


class Sink:
    def __init__(self):
        self.sent = []

    async def __call__(self, method, payload):
        self.sent.append((method, dict(payload)))


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R098")
async def test_golden_mocks_real_live_and_two_separate_fields():
    mocks = {digest({"name": "lookup", "args": {"q": 1}}): "mocked"}
    golden = SkillEvalCase("c1", "golden", "p", mocks)
    sink = Sink()

    async def drive(case, router, options, seed):
        router.use_skill("s")
        return await router.call_tool("lookup", {"q": 1})

    result = await run_eval_case(
        golden, new_workspace=LocalWorkspace, drive=drive, submit=sink
    )
    assert result.actual_trajectory == (("skill", "s"), ("tool", "lookup"))
    assert result.output_digest == digest("mocked") and result.error_code is None
    assert sink.sent[0][0] == "SkillEvalResult"

    async def unmocked(case, router, options, seed):
        return await router.call_tool("other", {})

    missing = await run_eval_case(
        golden, new_workspace=LocalWorkspace, drive=unmocked, submit=sink
    )
    assert missing.error_code == "MOCK_MISSING" and missing.output_digest is None

    live_calls = []

    async def live(name, args):
        live_calls.append(name)
        return "real"

    real = SkillEvalCase("c2", "real", "p")
    live_result = await run_eval_case(
        real, new_workspace=LocalWorkspace, drive=unmocked, submit=sink, live=live
    )
    assert live_calls == ["other"]
    assert live_result.output_digest == digest("real")
