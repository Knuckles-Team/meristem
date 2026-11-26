"""The one engine call of every consumer step: port check, call, refusal pass-through.

A consumer step reaches the decision engine only through the bound
:class:`~meristem.host_ports.EngineDecisionPort`. Without a port it refuses
``engine_decision_unavailable``; an engine refusal code passes through as a
:class:`ConsumerRefused`. The harness decides nothing here.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from meristem.harness.plan import canonical_json
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineAnswer, EngineDecisionPort, EngineDecisionRefused


def digest_of(value: Any) -> str:
    """SHA-256 of the canonical JSON of one received value."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _checked(answer: EngineAnswer) -> EngineAnswer:
    if answer.refusal_code is not None:
        raise ConsumerRefused(answer.refusal_code)
    return answer


def _bound(port: EngineDecisionPort | None) -> EngineDecisionPort:
    if port is None:
        raise EngineDecisionRefused("engine_decision_unavailable")
    return port


async def decide(
    port: EngineDecisionPort | None,
    kind: str,
    params: Mapping[str, Any],
    *,
    run_ref: str,
) -> EngineAnswer:
    """One served decision; engine refusal codes pass through."""
    return _checked(await _bound(port).decide(kind, params, run_ref=run_ref))


async def read(
    port: EngineDecisionPort | None,
    method: str,
    params: Mapping[str, Any],
    *,
    run_ref: str,
) -> EngineAnswer:
    """One served read; engine refusal codes pass through."""
    return _checked(await _bound(port).read(method, params, run_ref=run_ref))


async def write(
    port: EngineDecisionPort | None,
    method: str,
    params: Mapping[str, Any],
    *,
    run_ref: str,
) -> EngineAnswer:
    """One served write; engine refusal codes pass through."""
    return _checked(await _bound(port).write(method, params, run_ref=run_ref))


async def record(
    port: EngineDecisionPort | None,
    op: str,
    body: Mapping[str, Any],
    *,
    run_ref: str,
) -> EngineAnswer:
    """One decision-log write: the op and its body in one engine call."""
    return _checked(await _bound(port).log(op, body, run_ref=run_ref))


__all__ = ["decide", "digest_of", "read", "record", "write"]
