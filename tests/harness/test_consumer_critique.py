"""Read trace critique pages in order (MERISTEM-HARNESS-CYCLE-R016.3)."""

from __future__ import annotations

import pytest
from support.recorded_engine import RecordedEnginePort

from meristem.harness.critique import read_critique
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineDecisionRefused

# spec: MERISTEM-HARNESS-CYCLE-R016.3

BASE = {"root_node_id": "n", "snapshot_id": "s", "limit": 2}


def _entry(cursor, next_cursor):
    return {
        "method": "TraceCritique",
        "params": {"query": {**BASE, "cursor": cursor}},
        "answer": {
            "outcome": "solved",
            "payload": {"nodes": [cursor], "next_cursor": next_cursor},
        },
    }


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R016.3")
async def test_pages_read_in_order():
    port = RecordedEnginePort(
        {"answers": [_entry(None, "c1"), _entry("c1", "c2"), _entry("c2", None)]}
    )
    pages = await read_critique(BASE, port, run_ref="r")
    assert [p["cursor"] for p in pages] == [None, "c1", "c2"]


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R016.3")
async def test_repeated_cursor_refused_and_unbound_port_refuses():
    port = RecordedEnginePort({"answers": [_entry(None, "c1"), _entry("c1", "c1")]})
    with pytest.raises(ConsumerRefused) as raised:
        await read_critique(BASE, port, run_ref="r")
    assert raised.value.code == "critique_page_out_of_order"
    with pytest.raises(EngineDecisionRefused):
        await read_critique(BASE, None, run_ref="r")
