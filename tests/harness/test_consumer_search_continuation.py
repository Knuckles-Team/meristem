"""Search state across continuation and compaction
(MERISTEM-HARNESS-CYCLE-R034.1)."""

from __future__ import annotations

import json

import pytest
from support.recorded_engine import RecordedEnginePort, params_digest

from meristem.harness import search_state as ss
from meristem.harness.run_lifecycle import ConsumerRefused

# spec: MERISTEM-HARNESS-CYCLE-R034.1

STATE = ss.SearchStateFields(("t1",), ("e1",), ("c1",), ("o1",))


def _document(state=STATE, revision=1):
    return json.dumps(
        {
            "search_run_id": "sr",
            "state_revision": revision,
            **{n: list(getattr(state, n)) for n in ss.FIELDS},
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _save_params(revision=1):
    return {
        "request": {
            "action": "push",
            "doc_id": "d1",
            "doc_kind": "search_state",
            "request_id": "q1",
        },
        "content": _document(revision=revision),
    }


async def _save(port, previous=0):
    return await ss.save_search_state(
        STATE,
        port,
        search_run_id="sr",
        doc_id="d1",
        previous_revision=previous,
        request_id="q1",
        run_ref="r",
    )


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R034.1")
async def test_continuation_passes_id_and_revision():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "SyncMemorySnapshot",
                    "params": _save_params(1),
                    "answer": {
                        "outcome": "solved",
                        "payload": {"outcome": "accepted", "head": {"revision": 1}},
                    },
                },
                {
                    "method": "ReadMemorySnapshot",
                    "params": {"doc_id": "d1", "revision": 1},
                    "answer": {"outcome": "solved", "payload": {"content": "x"}},
                },
            ]
        }
    )
    saved = await _save(port)
    assert (saved.doc_id, saved.revision) == ("d1", 1)
    await ss.load_search_state(
        port, doc_id=saved.doc_id, revision=saved.revision, run_ref="r"
    )
    assert port.calls[-1] == (
        "ReadMemorySnapshot",
        params_digest({"doc_id": "d1", "revision": 1}),
    )


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R034.1")
async def test_stale_save_rereads_latest_revision():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "SyncMemorySnapshot",
                    "params": _save_params(4),
                    "answer": {
                        "outcome": "solved",
                        "payload": {"outcome": "conflict_retained"},
                    },
                },
                {
                    "method": "ReadMemorySnapshot",
                    "params": {"doc_id": "d1"},
                    "answer": {"outcome": "solved", "payload": {"content": "x"}},
                },
            ]
        }
    )
    with pytest.raises(ConsumerRefused) as raised:
        await _save(port, previous=3)
    assert raised.value.code == "SEARCH_STATE_REVISION_MISMATCH"
    assert [c[0] for c in port.calls] == ["SyncMemorySnapshot", "ReadMemorySnapshot"]
