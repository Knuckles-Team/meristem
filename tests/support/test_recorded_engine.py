"""The recorded engine port replays by params digest and fails on gaps."""

from __future__ import annotations

import pytest
import recorded_engine as recorded


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R077")
async def test_unrecorded_call_fails_and_recorded_call_replays():
    port = recorded.RecordedEnginePort("recorded_case")
    assert port.source_test
    assert port.engine_commit
    params = {"query": "champion skill", "tenant": "test"}

    # The first call for method+params digest replays the first record.
    first = await port.read("MemoryRecall", params, run_ref="r1")
    assert first.outcome == "solved"
    assert first.option_ids == ("opt-recall-1",)
    assert first.record_digest == (
        "9c1b4d7e2f8a3c5b6d0e9f8a7b6c5d4e3f2a1b0c9d8e7f6a5b4c3d2e1f0a9b8c"
    )
    assert first.payload == {"memory": {"ref": "mem-1"}}

    # The next call for the same method and digest replays the next record.
    second = await port.read("MemoryRecall", params, run_ref="r1")
    assert (second.outcome, second.refusal_code) == (
        "abstained",
        "NO_OUTCOME_HISTORY",
    )

    # A third call for the exhausted key is unrecorded and fails the test.
    with pytest.raises(recorded.UnrecordedEngineCall) as exhausted:
        await port.read("MemoryRecall", params, run_ref="r1")
    assert exhausted.value.method == "MemoryRecall"
    assert exhausted.value.digest == recorded.params_digest(params)

    # Any other digest for the method is unrecorded as well.
    with pytest.raises(recorded.UnrecordedEngineCall):
        await port.read("MemoryRecall", {"query": "other"}, run_ref="r1")

    # A recorded write replays under the same digest rule.
    answer = await port.write("MemoryRemember", {"content": "note one"}, run_ref="r1")
    assert answer.outcome == "committed"
    assert answer.record_digest == (
        "4e8a2c6d1b9f3e5a7c0d8b6f4a2e9c1d5b3f7a9e0c4d6b8f2a1e5c7d9b0f3a4e"
    )

    # Every call, recorded or not, is retained for the test's assertion.
    assert [method for method, _ in port.calls] == [
        "MemoryRecall",
        "MemoryRecall",
        "MemoryRecall",
        "MemoryRecall",
        "MemoryRemember",
    ]


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R077")
async def test_recorded_port_takes_a_fixture_dict():
    port = recorded.RecordedEnginePort(
        {
            "source_test": "dict-case",
            "engine_commit": "c" * 64,
            "answers": [
                {
                    "method": "Loops",
                    "params": {"loop_id": "l1"},
                    "answer": {"outcome": "solved", "payload": {"epoch": 3}},
                }
            ],
        }
    )
    assert port.source_test == "dict-case"
    answer = await port.read("Loops", {"loop_id": "l1"}, run_ref="r1")
    assert (answer.outcome, answer.payload) == ("solved", {"epoch": 3})
    with pytest.raises(recorded.UnrecordedEngineCall):
        await port.read("Loops", {"loop_id": "l2"}, run_ref="r1")
