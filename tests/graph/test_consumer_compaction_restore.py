"""Restore all four search-state fields before a compacted summary
(MERISTEM-HARNESS-CYCLE-R034.2)."""

from __future__ import annotations

import json

import pytest
from support.recorded_engine import RecordedEnginePort

from meristem.harness import search_state as ss
from meristem.harness.run_lifecycle import ConsumerRefused

# spec: MERISTEM-HARNESS-CYCLE-R034.2

STATE = ss.SearchStateFields(("t1",), ("e1",), ("c1",), ("o1",))


def _snapshot(digests_override=None, drop=None):
    document = {n: list(getattr(STATE, n)) for n in ss.FIELDS}
    if drop:
        del document[drop]
    evidence = {f"{n}_digest": ss.field_digest(getattr(STATE, n)) for n in ss.FIELDS}
    evidence.update(digests_override or {})
    return {
        "method": "ReadMemorySnapshot",
        "params": {"doc_id": "d1"},
        "answer": {
            "outcome": "solved",
            "payload": {"content": json.dumps(document), "search_state": evidence},
        },
    }


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R034.2")
async def test_fields_restored_before_summary():
    port = RecordedEnginePort({"answers": [_snapshot()]})
    assert await ss.restore_fields(port, doc_id="d1", run_ref="r") == STATE


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R034.2")
@pytest.mark.parametrize(
    "entry",
    [_snapshot({"evidence_digest": "bad"}), _snapshot(drop="open_claims")],
)
async def test_missing_or_mismatched_field_refuses(entry):
    with pytest.raises(ConsumerRefused) as raised:
        await ss.restore_fields(
            RecordedEnginePort({"answers": [entry]}), doc_id="d1", run_ref="r"
        )
    assert raised.value.code == "search_state_incomplete"
