"""Render received context facts as cited prose and compile the prompt
(MERISTEM-HARNESS-CYCLE-R036.1, MERISTEM-HARNESS-CYCLE-R036.2)."""

from __future__ import annotations

import pytest
from support.recorded_engine import RecordedEnginePort

from meristem.harness import context_prose as cp
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineDecisionRefused

# spec: MERISTEM-HARNESS-CYCLE-R036.1
# spec: MERISTEM-HARNESS-CYCLE-R036.2

PARAMS = {"node_ids": ["n1"], "token_budget": 50}


def _port(facts, omitted=0, refusal=None):
    answer = (
        {"outcome": "abstained", "refusal_code": refusal}
        if refusal
        else {
            "outcome": "solved",
            "payload": {"facts": facts, "omitted_count": omitted},
        }
    )
    return RecordedEnginePort(
        {"answers": [{"method": "ContextFacts", "params": PARAMS, "answer": answer}]}
    )


FACT = {"fact_id": "f1", "subject": "Ünïcode", "predicate": "owns", "object": "x"}


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R036.1")
async def test_facts_render_with_citations():
    result = await cp.read_facts(
        _port([FACT]), node_ids=["n1"], token_budget=50, run_ref="r"
    )
    rendered = cp.render_facts(result)
    assert [(r.fact_id, r.sentence) for r in rendered] == [
        ("f1", "Ünïcode owns x. [f1]")
    ]


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R036.1")
@pytest.mark.parametrize("blank", ["subject", "predicate", "object"])
async def test_unrenderable_fact_refused(blank):
    result = await cp.read_facts(
        _port([{**FACT, blank: " "}]), node_ids=["n1"], token_budget=50, run_ref="r"
    )
    with pytest.raises(ConsumerRefused) as raised:
        cp.render_facts(result)
    assert raised.value.code == "fact_unrenderable"


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R036.1")
async def test_engine_code_passes_and_unbound_port_refuses():
    with pytest.raises(ConsumerRefused) as raised:
        await cp.read_facts(
            _port([], refusal="CONTEXT_FACTS_BUDGET_ZERO"),
            node_ids=["n1"],
            token_budget=50,
            run_ref="r",
        )
    assert raised.value.code == "CONTEXT_FACTS_BUDGET_ZERO"
    with pytest.raises(EngineDecisionRefused):
        await cp.read_facts(None, node_ids=["n1"], token_budget=50, run_ref="r")


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R036.2")
async def test_prompt_byte_count_exact():
    result = await cp.read_facts(
        _port([FACT], omitted=3), node_ids=["n1"], token_budget=50, run_ref="r"
    )
    compiled = cp.compile_context(
        cp.render_facts(result),
        demonstrations=("Example: ✓",),
        omitted_count=result.omitted_count,
    )
    assert compiled.byte_count == len(compiled.text.encode("utf-8"))
    assert compiled.byte_count > len(compiled.text)  # multibyte facts
    assert compiled.fact_ids == ("f1",)
    assert compiled.omitted_count == 3
    # Prose sits before, and outside, the demonstration region.
    assert compiled.text.endswith("Example: ✓")
    assert compiled.text.index("[f1]") < compiled.text.index("Example")


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R036.2")
def test_byte_count_mismatch_refused():
    with pytest.raises(ConsumerRefused) as raised:
        cp.compile_context([], demonstrations=("é",), reported_byte_count=1)
    assert raised.value.code == "byte_count_mismatch"
