"""Choose direct or recursive execution (MERISTEM-HARNESS-CYCLE-R017.3)."""

from __future__ import annotations

import hashlib

import ports_support as support
import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from support.recorded_engine import RecordedEnginePort, params_digest

from meristem.graph.budget import SharedBudget
from meristem.graph.state import RunBudget
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineDecisionRefused
from meristem.rlm import RLMConfig
from meristem.rlm import runner as rlm_runner

SPEC = "MERISTEM-HARNESS-CYCLE-R017.3"
# spec: MERISTEM-HARNESS-CYCLE-R017.3


def _fit_params(prompt_tokens: int) -> dict:
    return {
        "prompt_tokens": prompt_tokens,
        "window_tokens": rlm_runner.DEFAULT_WINDOW_TOKENS,
        "model_profile_ref": "rlm",
    }


def _signals(output: str, ref: str) -> dict:
    signals = rlm_runner.ContextBudgetSignals(
        run_id="rlm",
        tool_output_bytes=len(output.encode("utf-8")),
        tool_output_digest=hashlib.sha256(output.encode("utf-8")).hexdigest(),
        tool_call_ref=ref,
    )
    return signals.params()


def _env() -> rlm_runner._Env:
    return rlm_runner._Env(
        text="abcd",
        depth=0,
        config=RLMConfig(),
        budget=SharedBudget(RunBudget()),
        ports=support.PORTS,
    )


async def test_each_mode_runs_as_decided():
    child_calls: list[str] = []

    def direct_model(messages, info):
        return ModelResponse(parts=[TextPart("direct-answer")])

    def child_model(messages, info):
        child_calls.append("child")
        return ModelResponse(parts=[TextPart("child-answer")])

    def root(messages, info):
        returns = [
            part
            for message in messages
            for part in message.parts
            if getattr(part, "part_kind", "") == "tool-return"
        ]
        if not returns:
            return ModelResponse(
                parts=[ToolCallPart("read_input", {"start": 0, "end": 4})]
            )
        if len(returns) < 3:
            return ModelResponse(
                parts=[
                    ToolCallPart("sub_query", {"question": "q", "start": 0, "end": 4})
                ]
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
                        "payload": {"source": "abcd"},
                    },
                },
                {
                    "method": "recursion_mode",
                    "params": _signals("abcd", "read_input"),
                    "answer": {"outcome": "committed", "payload": {"mode": "direct"}},
                },
                {
                    "method": "recursion_mode",
                    "params": _signals("direct-answer", "sub_query"),
                    "answer": {
                        "outcome": "committed",
                        "payload": {"mode": "recursive"},
                    },
                },
                {
                    "method": "recursion_mode",
                    "params": _signals("child-answer", "sub_query"),
                    "answer": {
                        "outcome": "committed",
                        "payload": {"mode": "recursive"},
                    },
                },
            ],
        }
    )
    support.register_engine_decision_port(port)
    support.register_model_provider(lambda model_id: FunctionModel(direct_model))

    result = await rlm_runner.run_rlm(
        "t",
        "abcd",
        config=RLMConfig(
            model=FunctionModel(root), sub_model=FunctionModel(child_model)
        ),
        ports=support.PORTS,
    )

    assert result["ok"] is True
    assert result["result"] == "child-answer"
    # The first mode ran direct (no child run); the second ran recursive.
    assert child_calls == ["child"]
    assert port.calls == [
        ("context_budget", params_digest(_fit_params(1))),
        ("ResearchNext", params_digest({})),
        ("recursion_mode", params_digest(_signals("abcd", "read_input"))),
        (
            "recursion_mode",
            params_digest(_signals("direct-answer", "sub_query")),
        ),
        (
            "recursion_mode",
            params_digest(_signals("child-answer", "sub_query")),
        ),
    ]


async def test_unbound_port_refuses_and_decides_nothing():
    state = _env()

    with pytest.raises(EngineDecisionRefused, match="engine_decision_unavailable"):
        await rlm_runner.choose_mode(state, "abcd", support.PORTS)
    assert state.recursion_mode is None


async def test_missing_tool_call_ref_refuses():
    state = _env()
    support.register_engine_decision_port(RecordedEnginePort({"answers": []}))

    with pytest.raises(ConsumerRefused, match="signal_incomplete"):
        await rlm_runner.choose_mode(state, "abcd", support.PORTS)
    assert state.recursion_mode is None


async def test_engine_refusal_passes_through():
    state = _env()
    state.tool_call_ref = "read_input"
    support.register_engine_decision_port(
        RecordedEnginePort(
            {
                "answers": [
                    {
                        "method": "recursion_mode",
                        "params": _signals("abcd", "read_input"),
                        "answer": {
                            "outcome": "abstained",
                            "refusal_code": "CONTEXT_SIGNALS_STALE",
                        },
                    }
                ],
            }
        )
    )

    with pytest.raises(ConsumerRefused, match="CONTEXT_SIGNALS_STALE"):
        await rlm_runner.choose_mode(state, "abcd", support.PORTS)


async def test_malformed_mode_refuses():
    state = _env()
    state.tool_call_ref = "read_input"
    support.register_engine_decision_port(
        RecordedEnginePort(
            {
                "answers": [
                    {
                        "method": "recursion_mode",
                        "params": _signals("abcd", "read_input"),
                        "answer": {
                            "outcome": "committed",
                            "payload": {"mode": "sideways"},
                        },
                    }
                ],
            }
        )
    )

    with pytest.raises(ConsumerRefused, match="recursion_mode_invalid"):
        await rlm_runner.choose_mode(state, "abcd", support.PORTS)
    assert state.recursion_mode is None


async def test_abstained_answer_keeps_the_current_mode():
    state = _env()
    state.tool_call_ref = "read_input"
    state.recursion_mode = "recursive"
    support.register_engine_decision_port(
        RecordedEnginePort(
            {
                "answers": [
                    {
                        "method": "recursion_mode",
                        "params": _signals("abcd", "read_input"),
                        "answer": {"outcome": "advisory"},
                    }
                ],
            }
        )
    )

    mode = await rlm_runner.choose_mode(state, "abcd", support.PORTS)

    assert mode == "recursive"
    assert state.recursion_mode == "recursive"
