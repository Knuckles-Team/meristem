"""Read source text through ReadSpan (MERISTEM-HARNESS-CYCLE-R037.1)."""

from __future__ import annotations

import hashlib

import ports_support as support
import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from support.recorded_engine import RecordedEnginePort, params_digest

import meristem.rlm.span_reads as span_reads
from meristem.graph.budget import SharedBudget
from meristem.graph.live_events import LineageBinding
from meristem.graph.state import RunBudget
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineDecisionRefused
from meristem.rlm import RLMConfig
from meristem.rlm import runner as rlm_runner

SPEC = "MERISTEM-HARNESS-CYCLE-R037.1"
# spec: MERISTEM-HARNESS-CYCLE-R037.1


def _fit_params(prompt_tokens: int) -> dict:
    return {
        "prompt_tokens": prompt_tokens,
        "window_tokens": rlm_runner.DEFAULT_WINDOW_TOKENS,
        "model_profile_ref": "rlm",
    }


def _span_params() -> dict:
    return {
        "document_id": "doc-1",
        "revision": "r1",
        "line_start": 1,
        "line_end": 3,
        "toc_digest": "toc-d",
    }


def _signals(run_id: str, output: str, ref: str) -> dict:
    signals = rlm_runner.ContextBudgetSignals(
        run_id=run_id,
        tool_output_bytes=len(output.encode("utf-8")),
        tool_output_digest=hashlib.sha256(output.encode("utf-8")).hexdigest(),
        tool_call_ref=ref,
    )
    return signals.params()


def _env(toc_digest: str | None = "toc-d") -> rlm_runner._Env:
    return rlm_runner._Env(
        text="",
        depth=0,
        config=RLMConfig(),
        budget=SharedBudget(RunBudget()),
        ports=support.PORTS,
        toc_digest=toc_digest,
    )


async def test_reads_use_read_span_with_digest(events):
    def root(messages, info: AgentInfo):
        returns = [
            part
            for message in messages
            for part in message.parts
            if getattr(part, "part_kind", "") == "tool-return"
        ]
        if not returns:
            return ModelResponse(
                parts=[ToolCallPart("read_input", {"start": 1, "end": 3})]
            )
        return ModelResponse(parts=[TextPart(str(returns[-1].content))])

    port = RecordedEnginePort(
        {
            "source_test": __file__,
            "engine_commit": "test",
            "answers": [
                {
                    "method": "context_budget",
                    "params": _fit_params(1),
                    "answer": {
                        "outcome": "committed",
                        "payload": {
                            "exceeds": False,
                            "chunks": 0,
                            "depth": 0,
                            "chunk_tokens": 0,
                            "model_calls": 0,
                        },
                    },
                },
                {
                    "method": "ResearchNext",
                    "params": {},
                    "answer": {
                        "outcome": "committed",
                        "record_digest": "research-next-1",
                        "payload": {"source": "doc-1"},
                    },
                },
                {
                    "method": "ReadSpan",
                    "params": _span_params(),
                    "answer": {
                        "outcome": "committed",
                        "payload": {"lines": ["line one", "line two"]},
                    },
                },
                {
                    "method": "recursion_mode",
                    "params": _signals("run", "line one\nline two", "read_input"),
                    "answer": {
                        "outcome": "committed",
                        "payload": {"mode": "recursive"},
                    },
                },
            ],
        }
    )
    support.register_engine_decision_port(port)
    support.register_model_provider(lambda model_id: FunctionModel(root))

    result = await rlm_runner.run_rlm(
        "read",
        config=RLMConfig(model=FunctionModel(root)),
        document={"document_id": "doc-1", "revision": "r1", "toc_digest": "toc-d"},
        lineage=LineageBinding("run", "root", "root"),
        ports=support.PORTS,
    )

    assert result["ok"] is True
    assert result["result"] == "line one\nline two"
    assert port.calls == [
        ("context_budget", params_digest(_fit_params(1))),
        ("ResearchNext", params_digest({})),
        ("ReadSpan", params_digest(_span_params())),
        (
            "recursion_mode",
            params_digest(_signals("run", "line one\nline two", "read_input")),
        ),
    ]
    reads = [event for event in events if event["type"] == "span_read"]
    assert len(reads) == 1
    assert reads[0]["document_id"] == "doc-1"
    assert reads[0]["toc_digest"] == "toc-d"
    assert reads[0]["line_start"] == 1
    assert reads[0]["line_end"] == 3


async def test_read_without_toc_digest_refuses():
    def root(messages, info: AgentInfo):
        if not any(
            getattr(part, "part_kind", "") == "tool-return"
            for message in messages
            for part in message.parts
        ):
            return ModelResponse(
                parts=[ToolCallPart("read_input", {"start": 1, "end": 3})]
            )
        return ModelResponse(parts=[TextPart("never")])

    support.register_engine_decision_port(
        RecordedEnginePort(
            {
                "answers": [
                    {
                        "method": "context_budget",
                        "params": _fit_params(1),
                        "answer": {
                            "outcome": "committed",
                            "payload": {
                                "exceeds": False,
                                "chunks": 0,
                                "depth": 0,
                                "chunk_tokens": 0,
                                "model_calls": 0,
                            },
                        },
                    },
                    {
                        "method": "ResearchNext",
                        "params": {},
                        "answer": {
                            "outcome": "committed",
                            "payload": {"source": "doc-1"},
                        },
                    },
                ],
            }
        )
    )

    result = await rlm_runner.run_rlm(
        "read",
        config=RLMConfig(model=FunctionModel(root)),
        document={"document_id": "doc-1", "revision": "r1"},
        ports=support.PORTS,
    )

    assert result["ok"] is False
    assert "toc_digest_missing" in result["error"]


async def test_engine_span_codes_pass_through():
    port = RecordedEnginePort(
        {
            "answers": [
                {
                    "method": "ReadSpan",
                    "params": _span_params(),
                    "answer": {
                        "outcome": "abstained",
                        "refusal_code": "SPAN_OUT_OF_BOUNDS",
                    },
                }
            ],
        }
    )
    support.register_engine_decision_port(port)

    with pytest.raises(ConsumerRefused, match="SPAN_OUT_OF_BOUNDS"):
        await span_reads.read_span(_env(), "doc-1", "r1", 1, 3)


async def test_unbound_port_refuses_and_decides_nothing():
    with pytest.raises(EngineDecisionRefused, match="engine_decision_unavailable"):
        await span_reads.read_span(_env(), "doc-1", "r1", 1, 3)


async def test_unserved_read_refuses():
    support.register_engine_decision_port(
        RecordedEnginePort(
            {
                "answers": [
                    {
                        "method": "ReadSpan",
                        "params": _span_params(),
                        "answer": {"outcome": "abstained"},
                    }
                ],
            }
        )
    )

    with pytest.raises(ConsumerRefused, match="span_read_unserved"):
        await span_reads.read_span(_env(), "doc-1", "r1", 1, 3)


async def test_malformed_lines_refuse():
    support.register_engine_decision_port(
        RecordedEnginePort(
            {
                "answers": [
                    {
                        "method": "ReadSpan",
                        "params": _span_params(),
                        "answer": {
                            "outcome": "committed",
                            "payload": {"lines": "not a list"},
                        },
                    }
                ],
            }
        )
    )

    with pytest.raises(ConsumerRefused, match="span_read_unserved"):
        await span_reads.read_span(_env(), "doc-1", "r1", 1, 3)
