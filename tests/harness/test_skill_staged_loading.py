"""Three-stage skill loading (MERISTEM-HARNESS-CYCLE-R094)."""

from __future__ import annotations

import pytest

from meristem.harness.skill_loader import (
    LoadBudgets,
    SkillLoader,
    SkillStageOutOfOrder,
)


class Source:
    async def metadata(self, ref):
        return ref, "d" * 1000

    async def body(self, ref):
        return "\n".join(f"line {i}" for i in range(10))

    async def reference(self, ref, path):
        return f"ref {path}"

    async def load_script(self, ref):
        return None


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R094")
async def test_three_stages_events_budgets_and_order_refusal():
    events = []
    loader = SkillLoader(
        Source(),
        events.append,
        budgets=LoadBudgets(stage1_max_tokens=10, stage2_max_lines=4),
    )
    with pytest.raises(SkillStageOutOfOrder) as refused:
        await loader.stage3("a", "references/x.md")
    assert refused.value.code == "SKILL_STAGE_OUT_OF_ORDER"
    assert events == []

    first = await loader.stage1(["a", "b"])
    assert [e.stage for e in events] == [1, 1]
    assert all(e.token_count <= 10 for e in events) and len(first) == 2

    body = await loader.stage2("a")
    assert body.truncated and body.text.count("\n") == 3
    ref = await loader.stage3("a", "references/x.md")
    assert ref.text == "ref references/x.md"
    assert [(e.skill_ref, e.stage) for e in events[2:]] == [("a", 2), ("a", 3)]
    # A stage 3 load of a skill whose body was not loaded stays refused.
    with pytest.raises(SkillStageOutOfOrder):
        await loader.stage3("b", "references/x.md")

    # Fallback defaults apply when no budget is received.
    assert SkillLoader(Source(), events.append).budgets == LoadBudgets(100, 500)
