"""Render engine-supplied context facts as cited prose (one context compile).

The harness decides nothing here: the engine picks the facts, the harness
writes each one as a sentence with its ``[fact_id]`` citation and reports the
exact UTF-8 size of the final prompt text.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineDecisionPort, EngineDecisionRefused

READ_METHOD = "ContextFacts"


@dataclass(frozen=True, slots=True)
class ContextFact:
    """One received fact."""

    fact_id: str
    subject: str
    predicate: str
    object: str


@dataclass(frozen=True, slots=True)
class ContextFactsResult:
    """The received ``ContextFacts`` answer."""

    facts: tuple[ContextFact, ...]
    omitted_count: int = 0


@dataclass(frozen=True, slots=True)
class RenderedFact:
    """One fact as a sentence, with the citation it keeps in the trace."""

    fact_id: str
    sentence: str


@dataclass(frozen=True, slots=True)
class CompiledContext:
    """The final prompt text with its exact UTF-8 byte count."""

    text: str
    byte_count: int
    fact_ids: tuple[str, ...]
    omitted_count: int


def parse_facts(payload: Mapping[str, Any]) -> ContextFactsResult:
    """Read the received answer payload; a malformed one is ``fact_unrenderable``."""
    raw = payload.get("facts")
    if not isinstance(raw, (list, tuple)):
        raise ConsumerRefused("fact_unrenderable")
    facts: list[ContextFact] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            raise ConsumerRefused("fact_unrenderable")
        facts.append(
            ContextFact(
                fact_id=str(entry.get("fact_id", "")),
                subject=str(entry.get("subject", "")),
                predicate=str(entry.get("predicate", "")),
                object=str(entry.get("object", "")),
            )
        )
    omitted = payload.get("omitted_count", 0)
    if type(omitted) is not int or omitted < 0:
        raise ConsumerRefused("fact_unrenderable")
    return ContextFactsResult(tuple(facts), omitted)


async def read_facts(
    port: EngineDecisionPort | None,
    *,
    node_ids: Sequence[str],
    token_budget: int,
    max_facts_per_node: int | None = None,
    run_ref: str,
) -> ContextFactsResult:
    """Read the facts for ``node_ids``; engine codes pass through."""
    if port is None:
        raise EngineDecisionRefused("engine_decision_unavailable")
    params: dict[str, Any] = {
        "node_ids": list(node_ids),
        "token_budget": token_budget,
    }
    if max_facts_per_node is not None:
        params["max_facts_per_node"] = max_facts_per_node
    answer = await port.read(READ_METHOD, params, run_ref=run_ref)
    if answer.refusal_code is not None:
        raise ConsumerRefused(answer.refusal_code)
    return parse_facts(answer.payload)


def render_facts(result: ContextFactsResult) -> tuple[RenderedFact, ...]:
    """One sentence per fact: subject, predicate, object, then ``[fact_id]``.

    A fact with an empty subject, predicate, object or id refuses
    ``fact_unrenderable``; no prose is invented for it.
    """
    rendered: list[RenderedFact] = []
    for fact in result.facts:
        parts = (fact.subject.strip(), fact.predicate.strip(), fact.object.strip())
        if not all(parts) or not fact.fact_id.strip():
            raise ConsumerRefused("fact_unrenderable")
        rendered.append(
            RenderedFact(fact.fact_id, f"{' '.join(parts)}. [{fact.fact_id}]")
        )
    return tuple(rendered)


def compile_context(
    facts: Sequence[RenderedFact],
    *,
    demonstrations: Sequence[str] = (),
    omitted_count: int = 0,
    reported_byte_count: int | None = None,
) -> CompiledContext:
    """Join the prose, outside the demonstration regions, into the prompt text.

    ``byte_count`` is the UTF-8 length of the text. A
    ``reported_byte_count`` that differs refuses ``byte_count_mismatch``.
    """
    prose = "\n".join(fact.sentence for fact in facts)
    text = "\n\n".join(part for part in (prose, *demonstrations) if part)
    count = len(text.encode("utf-8"))
    if reported_byte_count is not None and reported_byte_count != count:
        raise ConsumerRefused("byte_count_mismatch")
    return CompiledContext(
        text=text,
        byte_count=count,
        fact_ids=tuple(fact.fact_id for fact in facts),
        omitted_count=omitted_count,
    )
