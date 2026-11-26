"""Split an over-window prompt (MERISTEM-HARNESS-CYCLE-R017.1)."""

from __future__ import annotations

import ports_support as support
import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from support.recorded_engine import RecordedEnginePort, params_digest

from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineDecisionRefused
from meristem.rlm import RLMConfig
from meristem.rlm import runner as rlm_runner

SPEC = "MERISTEM-HARNESS-CYCLE-R017.1"
# spec: MERISTEM-HARNESS-CYCLE-R017.1


def _params(prompt_tokens: int) -> dict:
    return {
        "prompt_tokens": prompt_tokens,
        "window_tokens": rlm_runner.DEFAULT_WINDOW_TOKENS,
        "model_profile_ref": "rlm",
    }


def _splitting_answer(model_calls: int = 7) -> dict:
    return {
        "outcome": "committed",
        "record_digest": "split-record",
        "payload": {
            "exceeds": True,
            "chunks": 4,
            "depth": 2,
            "chunk_tokens": 10,
            "model_calls": model_calls,
        },
    }


async def test_four_chunks_make_seven_model_calls():
    prompts: list[str] = []

    def model(messages, info):
        prompt = messages[0].parts[0].content
        prompts.append(prompt)
        if "[chunk " in prompt:
            number = prompt.split("[chunk ", 1)[1].split(" of", 1)[0]
            return ModelResponse(parts=[TextPart(f"leaf {number}")])
        return ModelResponse(parts=[TextPart("merged")])

    port = RecordedEnginePort(
        {
            "source_test": __file__,
            "engine_commit": "test",
            "answers": [
                {
                    "method": "context_budget",
                    "params": _params(10),
                    "answer": _splitting_answer(),
                }
            ],
        }
    )
    support.register_engine_decision_port(port)
    support.register_model_provider(lambda model_id: FunctionModel(model))

    result = await rlm_runner.run_rlm(
        "summarize",
        "x" * 40,
        config=RLMConfig(model="rlm"),
        ports=support.PORTS,
    )

    assert result["ok"] is True
    assert result["result"] == "merged"
    assert result["split_model_calls"] == 7
    leaves = [prompt for prompt in prompts if "[chunk " in prompt]
    merges = [prompt for prompt in prompts if "[merge]" in prompt]
    assert len(leaves) == 4
    assert len(merges) == 3
    assert len(prompts) == 7
    assert port.calls == [("context_budget", params_digest(_params(10)))]


async def test_fitting_prompt_runs_whole():
    support.register_engine_decision_port(
        RecordedEnginePort(
            {
                "answers": [
                    {
                        "method": "context_budget",
                        "params": _params(1),
                        "answer": {
                            "outcome": "committed",
                            "record_digest": "fit-record",
                            "payload": {
                                "exceeds": False,
                                "chunks": 0,
                                "depth": 0,
                                "chunk_tokens": 0,
                                "model_calls": 0,
                            },
                        },
                    }
                ],
            }
        )
    )

    result = await rlm_runner.plan_split("t", "abcd", support.PORTS)

    assert (result.split, result.answer, result.model_calls) == (False, None, 0)
    assert result.record_digest == "fit-record"


async def test_unbound_port_refuses_and_decides_nothing():
    def model(messages, info):
        pytest.fail("the model must not be called")

    support.register_model_provider(lambda model_id: FunctionModel(model))

    with pytest.raises(EngineDecisionRefused, match="engine_decision_unavailable"):
        await rlm_runner.plan_split("t", "x" * 40, support.PORTS)


async def test_engine_refusal_passes_through():
    support.register_engine_decision_port(
        RecordedEnginePort(
            {
                "answers": [
                    {
                        "method": "context_budget",
                        "params": _params(10),
                        "answer": {
                            "outcome": "abstained",
                            "refusal_code": "CONTEXT_WINDOW_EXHAUSTED",
                        },
                    }
                ],
            }
        )
    )

    with pytest.raises(ConsumerRefused, match="CONTEXT_WINDOW_EXHAUSTED"):
        await rlm_runner.plan_split("t", "x" * 40, support.PORTS)


async def test_plan_above_the_model_call_budget_refuses():
    support.register_engine_decision_port(
        RecordedEnginePort(
            {
                "answers": [
                    {
                        "method": "context_budget",
                        "params": _params(10),
                        "answer": _splitting_answer(model_calls=6),
                    }
                ],
            }
        )
    )

    with pytest.raises(ConsumerRefused, match="split_exceeds_model_calls"):
        await rlm_runner.plan_split("t", "x" * 40, support.PORTS)


async def test_malformed_split_answer_refuses():
    support.register_engine_decision_port(
        RecordedEnginePort(
            {
                "answers": [
                    {
                        "method": "context_budget",
                        "params": _params(10),
                        "answer": {
                            "outcome": "committed",
                            "payload": {
                                "exceeds": "yes",
                                "chunks": 4,
                                "depth": 2,
                                "chunk_tokens": 10,
                                "model_calls": 7,
                            },
                        },
                    }
                ],
            }
        )
    )

    with pytest.raises(ConsumerRefused, match="split_plan_invalid"):
        await rlm_runner.plan_split("t", "x" * 40, support.PORTS)
