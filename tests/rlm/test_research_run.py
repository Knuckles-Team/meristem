"""Apply the next-read and stop decision (MERISTEM-HARNESS-CYCLE-R047.1)."""

from __future__ import annotations

import hashlib

import ports_support as support
import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from support.recorded_engine import RecordedEnginePort, params_digest

import meristem.rlm.research_steps as research_steps
from meristem.graph.budget import SharedBudget
from meristem.graph.state import RunBudget
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineDecisionRefused
from meristem.rlm import RLMConfig
from meristem.rlm import runner as rlm_runner

SPEC = "MERISTEM-HARNESS-CYCLE-R047.1"
# spec: MERISTEM-HARNESS-CYCLE-R047.1


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


async def test_run_stops_when_decide_says_stop():
    seen: list[list] = []

    def root(messages, info: AgentInfo):
        seen.append(messages)
        if not any(
            getattr(part, "part_kind", "") == "tool-return"
            for message in messages
            for part in message.parts
        ):
            return ModelResponse(
                parts=[ToolCallPart("read_input", {"start": 0, "end": 4})]
            )
        return ModelResponse(parts=[TextPart("stopped")])

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
                        "record_digest": "research-next-stop",
                        "payload": {"stop": True, "reason": "budget spent"},
                    },
                },
                {
                    "method": "recursion_mode",
                    "params": _signals("research ended: budget spent", "read_input"),
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
        "research",
        "abcd",
        config=RLMConfig(model=FunctionModel(root)),
        ports=support.PORTS,
    )

    assert result["ok"] is True
    assert result["result"] == "stopped"
    # No read happened after the stop: the model saw the stop marker, not
    # a served slice.
    last_messages = seen[-1]
    returns = [
        part
        for message in last_messages
        for part in message.parts
        if getattr(part, "part_kind", "") == "tool-return"
    ]
    assert "research ended: budget spent" in str(returns[-1].content)
    assert port.calls == [
        ("context_budget", params_digest(_fit_params(1))),
        ("ResearchNext", params_digest({})),
        (
            "recursion_mode",
            params_digest(_signals("research ended: budget spent", "read_input")),
        ),
    ]


async def test_depth_exceeded_ends_the_run_and_passes_through():
    support.register_engine_decision_port(
        RecordedEnginePort(
            {
                "answers": [
                    {
                        "method": "ResearchNext",
                        "params": {},
                        "answer": {
                            "outcome": "abstained",
                            "refusal_code": "RESEARCH_DEPTH_EXCEEDED",
                            "record_digest": "depth-record",
                        },
                    }
                ],
            }
        )
    )

    step = await research_steps.next_read(_env())

    assert step.action == "stop"
    assert step.reason == "RESEARCH_DEPTH_EXCEEDED"
    assert step.decision_id == "depth-record"


async def test_no_budget_ends_the_run():
    support.register_engine_decision_port(
        RecordedEnginePort(
            {
                "answers": [
                    {
                        "method": "ResearchNext",
                        "params": {},
                        "answer": {
                            "outcome": "abstained",
                            "refusal_code": "RESEARCH_NO_BUDGET",
                        },
                    }
                ],
            }
        )
    )

    step = await research_steps.next_read(_env())

    assert (step.action, step.reason) == ("stop", "RESEARCH_NO_BUDGET")


async def test_other_engine_codes_refuse():
    support.register_engine_decision_port(
        RecordedEnginePort(
            {
                "answers": [
                    {
                        "method": "ResearchNext",
                        "params": {},
                        "answer": {
                            "outcome": "abstained",
                            "refusal_code": "RESEARCH_ENGINE_DOWN",
                        },
                    }
                ],
            }
        )
    )

    with pytest.raises(ConsumerRefused, match="RESEARCH_ENGINE_DOWN"):
        await research_steps.next_read(_env())


async def test_unbound_port_refuses_and_decides_nothing():
    with pytest.raises(EngineDecisionRefused, match="engine_decision_unavailable"):
        await research_steps.next_read(_env())


async def test_reads_the_returned_source():
    support.register_engine_decision_port(
        RecordedEnginePort(
            {
                "answers": [
                    {
                        "method": "ResearchNext",
                        "params": {},
                        "answer": {
                            "outcome": "committed",
                            "record_digest": "research-next-1",
                            "payload": {"source": "src-1"},
                        },
                    }
                ],
            }
        )
    )

    step = await research_steps.next_read(_env())

    assert (step.action, step.source, step.decision_id) == (
        "read",
        "src-1",
        "research-next-1",
    )
