"""N-ary hypotheses stay proposals; nothing is asserted or written."""

from __future__ import annotations

from support.recorded_engine import RecordedEnginePort

from meristem.harness.nary_proposals import read_nary_proposals

PARAMS = {"request": {"arity": 3}, "source": {"graph": "g"}}


async def test_hypotheses_stay_proposals():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "GraphLearnPredictNary",
                    "params": PARAMS,
                    "answer": {
                        "outcome": "advisory",
                        "payload": {
                            "graph_revision": 7,
                            "hypotheses": [
                                {"nodes": ["a", "b", "c"], "evidence_class": "inferred"}
                            ],
                        },
                    },
                }
            ]
        }
    )
    proposals = await read_nary_proposals(port, PARAMS, run_ref="r1")
    assert len(proposals) == 1
    assert proposals[0].asserted is False
    assert proposals[0].evidence_class == "inferred"
    assert proposals[0].revisions == {"graph_revision": 7}
    assert [method for method, _ in port.calls] == ["GraphLearnPredictNary"]
    assert port.bodies == []
