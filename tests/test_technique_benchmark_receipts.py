"""Five serving techniques each emit one bound baseline-and-candidate receipt."""

from __future__ import annotations

import pytest
from support.recorded_engine import RecordedEnginePort

from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.harness.technique_benchmark import TECHNIQUES, emit_technique_receipts

SPEC = "MERISTEM-HARNESS-CYCLE-R062"


@pytest.mark.spec(SPEC)
async def test_five_techniques_emit_bound_receipts():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "log",
                    "params": {"op": "Evaluate"},
                    "answer": {"outcome": "committed"},
                }
                for _ in TECHNIQUES
            ]
        }
    )
    seen = []

    async def measure(technique, arm, config_digest):
        seen.append((technique, arm, config_digest))
        return {"tokens_per_s": 10 if arm == "baseline" else 14}

    logged = await emit_technique_receipts(
        port, measure, run_ref="r1", model_id="m", config_digest="cfg"
    )
    assert logged == TECHNIQUES and len(TECHNIQUES) == 5
    assert all(config == "cfg" for _, _, config in seen)
    assert [(t, a) for t, a, _ in seen[:2]] == [
        ("paged_cache", "baseline"),
        ("paged_cache", "candidate"),
    ]
    assert len(port.bodies) == 5
    for (_, body), technique in zip(port.bodies, TECHNIQUES, strict=True):
        assert body["technique"] == technique and body["config_digest"] == "cfg"
        assert body["baseline"] == {"tokens_per_s": 10}
        assert body["candidate"] == {"tokens_per_s": 14}
    with pytest.raises(ConsumerRefused):
        await emit_technique_receipts(
            port,
            measure,
            run_ref="r",
            model_id="m",
            config_digest="c",
            techniques=("nope",),
        )
