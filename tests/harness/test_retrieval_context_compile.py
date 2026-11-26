"""The retrieval step renders the served ContextCompile bundle in served order."""

from __future__ import annotations

import pytest
from support.recorded_engine import RecordedEnginePort

from meristem.harness.retrieval_step import run_context_compile

SPEC = "MERISTEM-HARNESS-CYCLE-R067"
REQUEST = {"budget_tokens": 4096}


@pytest.mark.spec(SPEC)
async def test_retrieval_step_renders_context_compile_bundle():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "ContextCompile",
                    "params": {"request": REQUEST},
                    "answer": {
                        "outcome": "solved",
                        "payload": {
                            "items": [
                                {"text": "second chosen first", "citation": "doc-9"},
                                {"text": "then this"},
                            ]
                        },
                    },
                },
                {
                    "method": "ContextCompile",
                    "params": {"request": {"budget_tokens": 1}},
                    "answer": {
                        "outcome": "abstained",
                        "refusal_code": "BUDGET_TOO_SMALL",
                    },
                },
            ]
        }
    )
    text = await run_context_compile(port, REQUEST, run_ref="r1")
    assert text == "[1] second chosen first (doc-9)\n[2] then this"
    refused = await run_context_compile(port, {"budget_tokens": 1}, run_ref="r1")
    assert refused == "BUDGET_TOO_SMALL"
