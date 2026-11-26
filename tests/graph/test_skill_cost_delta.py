"""Skill cost delta (MERISTEM-HARNESS-CYCLE-R092)."""

from __future__ import annotations

import pytest

from meristem.harness.skill_events import PriceVersion, SkillEventRecorder


async def _noop(method, payload):
    return None


def _recorder(usage: list[tuple[int, int]], price):
    return SkillEventRecorder(
        run_id="r1",
        read_usage=lambda: usage[-1],
        ingest=_noop,
        price=price,
    )


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R092")
async def test_nested_spans_attribute_to_the_innermost_and_null_without_prices():
    usage = [(0, 0)]
    price = PriceVersion("v1", 1_000_000, 2_000_000)
    recorder = _recorder(usage, price)
    await recorder.on_skill_start("outer", "1")
    usage.append((100, 0))
    await recorder.on_skill_start("inner", "1")
    usage.append((300, 50))
    inner = await recorder.on_skill_end()
    usage.append((310, 60))
    outer = await recorder.on_skill_end()
    # inner: 200 in + 50 out = 200 + 100; outer keeps its own 110 in + 10 out.
    assert inner.cost_delta_micro_usd == 300
    assert outer.cost_delta_micro_usd == 110 + 20

    # With no received price the delta is null, never zero.
    bare = _recorder([(0, 0)], None)
    await bare.on_skill_start("s", "1")
    assert (await bare.on_skill_end()).cost_delta_micro_usd is None
