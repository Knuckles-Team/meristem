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
        tenant_id="t1",
        principal_pseudonym="p" * 64,
        span=lambda kind, payload: fake.spans.append((kind, dict(payload))),
        clock_ms=lambda: next(ticks),
    )
    lifecycle = RunLifecycle(consumers=("skill_events",))
    recorder.attach(lifecycle)
    await lifecycle.on_skill_start(
        {"skill_ref": "s/a", "skill_version": "1", "chosen_by": "user"}
    )
    await lifecycle.on_skill_end({"outcome": "error", "error_code": "SKILL_TIMEOUT"})
    # Only the finished invocation reaches the engine; the start is a span.
    ((method, call),) = fake.calls
    assert method == "SkillTelemetry" and call["op"] == "ingest"
    (event,) = call["events"]
    assert event["chosen_by"] == "user" and event["principal_pseudonym"] == "p" * 64
    assert (event["started_ms"], event["ended_ms"]) == (10, 20)
    assert event["outcome"] == {"error": {"code": "SKILL_TIMEOUT"}}
    assert event["tenant_id"] == "t1" and event["invocation_id"] == "r1:1"
    assert (event["tokens_in"], event["tokens_out"]) == (0, 0)
    assert not {"prompt", "args", "output", "error_code"} & set(event)
    assert [kind for kind, _ in fake.spans] == ["skill.start", "skill.end"]

    # A field outside the list blocks the event and is counted.
    assert not await recorder.on_skill_start("s/b", "1", prompt="secret")
    assert recorder.blocked == 1
    assert len(fake.calls) == 1
    # An error code outside the closed list is blocked too.
    assert await recorder.on_skill_start("s/b", "1")
    assert await recorder.on_skill_end(error_code="BOGUS") is None
    assert recorder.blocked == 2


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R091")
async def test_cancelled_maps_to_error_and_missing_method_is_unavailable():
    async def absent(method, payload):
        raise NotImplementedError(method)

    recorder = SkillEventRecorder(run_id="r2", read_usage=lambda: (3, 4), ingest=absent)
    assert await recorder.on_skill_start("s/a", "1")
    event = await recorder.on_skill_end(outcome="cancelled")
    assert event is not None and recorder.unavailable == 1
    assert event.as_invocation()["outcome"] == {"error": {"code": "SKILL_CANCELLED"}}
