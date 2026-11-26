"""Skill invocation events (MERISTEM-HARNESS-CYCLE-R091)."""

from __future__ import annotations

import pytest

from meristem.harness.run_lifecycle import RunLifecycle
from meristem.harness.skill_events import SkillEventRecorder


class Recording:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.spans: list[tuple[str, dict]] = []

    async def ingest(self, method, payload):
        self.calls.append((method, dict(payload)))


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R091")
async def test_event_names_chooser_and_carries_no_text():
    fake = Recording()
    ticks = iter(range(10, 1000, 10))
    recorder = SkillEventRecorder(
        run_id="r1",
        read_usage=lambda: (0, 0),
        ingest=fake.ingest,
        span=lambda kind, payload: fake.spans.append((kind, dict(payload))),
        clock_ms=lambda: next(ticks),
    )
    lifecycle = RunLifecycle(consumers=("skill_events",))
    recorder.attach(lifecycle)
    await lifecycle.on_skill_start(
        {"skill_ref": "s/a", "skill_version": "1", "chosen_by": "user"}
    )
    await lifecycle.on_skill_end({"outcome": "error", "error_code": "SKILL_TIMEOUT"})
    start, end = fake.calls
    assert start[0] == end[0] == "SkillTelemetryIngest"
    assert end[1]["chosen_by"] == "user"
    assert (end[1]["started_ms"], end[1]["ended_ms"]) == (10, 20)
    assert (end[1]["outcome"], end[1]["error_code"]) == ("error", "SKILL_TIMEOUT")
    assert not {"prompt", "args", "output"} & set(end[1])
    assert [kind for kind, _ in fake.spans] == ["skill.start", "skill.end"]

    # A field outside the list blocks the event and is counted.
    assert not await recorder.on_skill_start("s/b", "1", prompt="secret")
    assert recorder.blocked == 1
    assert len(fake.calls) == 2
    # An error code outside the closed list is blocked too.
    assert await recorder.on_skill_start("s/b", "1")
    assert await recorder.on_skill_end(error_code="BOGUS") is None
    assert recorder.blocked == 2
