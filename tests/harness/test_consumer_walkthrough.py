"""The walkthrough manifest binds spans and pinned sources; a stale source is refused."""

from __future__ import annotations

import pytest
from support.recorded_engine import RecordedEnginePort

from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.harness.walkthrough import (
    build_walkthrough_manifest,
    send_walkthrough_manifest,
)

SECTIONS = [{"path": "a.py", "source_revision": "rev1"}]
EVENTS = [{"task_id": "t0"}, {"span_id": "sp1", "path": "a.py"}]


async def test_manifest_binds_spans_and_sources():
    manifest = build_walkthrough_manifest(SECTIONS, EVENTS)
    assert manifest["sections"][0]["spans"] == [{"span_id": "sp1", "step": 1}]
    node = {"op": "add_node", "node": manifest}
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "AgentComponent",
                    "params": node,
                    "answer": {"outcome": "committed", "record_digest": "m-1"},
                }
            ]
        }
    )
    assert await send_walkthrough_manifest(port, manifest, run_ref="r1") == "m-1"
    stale = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "AgentComponent",
                    "params": node,
                    "answer": {
                        "outcome": "abstained",
                        "refusal_code": "SOURCE_REVISION_STALE",
                    },
                }
            ]
        }
    )
    with pytest.raises(ConsumerRefused, match="SOURCE_REVISION_STALE"):
        await send_walkthrough_manifest(stale, manifest, run_ref="r1")
    with pytest.raises(ConsumerRefused, match="WALKTHROUGH_SOURCE_UNPINNED"):
        build_walkthrough_manifest([{"path": "a.py"}], EVENTS)
