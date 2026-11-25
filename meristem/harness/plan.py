"""The plan-cluster consumers of the run loop.

Each consumer reaches the decision engine only through the host-bound
:class:`meristem.host_ports.EngineDecisionPort` and applies the answer
it receives without deciding anything of its own. A consumer whose port
is unbound refuses ``engine_decision_unavailable``; a malformed
received value refuses the consumer's closed local code; an engine
refusal passes through under the engine's closed code. The run loop
fails closed with the first refusal that reaches it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import (
    EngineAnswer,
    EngineDecisionPort,
    EngineDecisionRefused,
)


def canonical_json(value: Any) -> str:
    """Canonical JSON of one received value: sorted keys, no whitespace."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _step_graph(draft: Any) -> Mapping[str, Any]:
    """The received typed step graph; every step carries its type."""
    if not isinstance(draft, Mapping):
        raise ConsumerRefused("workflow_graph_invalid")
    steps = draft.get("steps")
    if not isinstance(steps, (list, tuple)) or not steps:
        raise ConsumerRefused("workflow_graph_invalid")
    for step in steps:
        if not isinstance(step, Mapping):
            raise ConsumerRefused("workflow_graph_invalid")
        step_type = step.get("type")
        if not isinstance(step_type, str) or not step_type.strip():
            raise ConsumerRefused("workflow_graph_invalid")
    return draft


@dataclass(frozen=True, slots=True)
class CompiledWorkflow:
    """The stored workflow graph and the engine's compile record."""

    source_digest: str
    program_ref: str | None
    record: EngineAnswer


async def store_then_compile(
    draft: Mapping[str, Any],
    port: EngineDecisionPort | None,
    *,
    run_ref: str,
) -> CompiledWorkflow:
    """Store the typed step graph, then record the engine's compile.

    The harness never compiles: it stores the graph it receives through
    ``put_blob`` and only then asks for the ``RunRecord`` compile
    record. The store call always precedes the compile call; a step
    without its type refuses ``workflow_graph_invalid`` before either
    call. The ``source_digest`` is the SHA-256 of the stored bytes; the
    compiled program ref is the record digest the engine returns.
    """
    if port is None:
        raise EngineDecisionRefused("engine_decision_unavailable")
    graph = _step_graph(draft)
    graph_json = canonical_json(graph)
    await port.put_blob(graph_json.encode("utf-8"), run_ref=run_ref)
    source_digest = hashlib.sha256(graph_json.encode("utf-8")).hexdigest()
    record = await port.log("RunRecord", run_ref=run_ref)
    if record.refusal_code is not None:
        raise ConsumerRefused(record.refusal_code)
    return CompiledWorkflow(
        source_digest=source_digest,
        program_ref=record.record_digest,
        record=record,
    )


__all__ = [
    "CompiledWorkflow",
    "canonical_json",
    "store_then_compile",
]
