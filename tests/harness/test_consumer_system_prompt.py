"""Apply the selected system prompt with a verified render receipt
(MERISTEM-HARNESS-CYCLE-R014.1)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import pytest

from meristem.harness import assembly
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineAnswer

SPEC = "MERISTEM-HARNESS-CYCLE-R014.1"
# spec: MERISTEM-HARNESS-CYCLE-R014.1

PROMPT_TEXT = "You are the run agent for this task."


@dataclass(frozen=True)
class MiniPlan:
    instructions: str = "default"


@pytest.mark.spec(SPEC)
def test_agent_starts_with_named_prompt():
    digest = hashlib.sha256(PROMPT_TEXT.encode("utf-8")).hexdigest()
    answer = EngineAnswer(
        outcome="solved",
        payload={
            "system_prompt": {
                "component_ref": "prompt.run-1",
                "digest": digest,
                "text": PROMPT_TEXT,
            }
        },
        record_digest="rec-1",
    )
    plan = MiniPlan()

    applied = assembly.apply_system_prompt(plan, answer)

    # The plan instructions become the verified prompt text.
    assert applied.instructions == PROMPT_TEXT
    assert applied is not plan


@pytest.mark.spec(SPEC)
def test_digest_mismatch_refuses_prompt_digest_mismatch():
    answer = EngineAnswer(
        outcome="solved",
        payload={
            "system_prompt": {
                "component_ref": "prompt.run-1",
                "digest": "0" * 64,
                "text": PROMPT_TEXT,
            }
        },
    )

    with pytest.raises(ConsumerRefused, match="prompt_digest_mismatch"):
        assembly.apply_system_prompt(MiniPlan(), answer)


@pytest.mark.spec(SPEC)
def test_missing_component_keeps_the_plan_instructions():
    answer = EngineAnswer(outcome="solved", payload={"primitives": []})
    plan = MiniPlan()

    applied = assembly.apply_system_prompt(plan, answer)

    assert applied is plan
