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


class ProfiledEnginePort:
    """The engine port of one run that carries the assistant profile ref.

    Arbora forwards ``message.metadata.assistant_profile_ref`` into the run
    request metadata. Every ``decide`` request of that run, including the
    ``harness_config`` request, carries it as ``assistant_profile_ref``
    (HarnessConfigRequestBody). The wrapper holds the ref and nothing else;
    a request that already names a profile keeps its own.
    """

    def __init__(self, inner: EngineDecisionPort, assistant_profile_ref: str) -> None:
        self._inner = inner
        self.assistant_profile_ref = assistant_profile_ref

    async def decide(
        self, kind: str, params: Mapping[str, Any], *, run_ref: str
    ) -> EngineAnswer:
        body = {"assistant_profile_ref": self.assistant_profile_ref, **params}
        return await self._inner.decide(kind, body, run_ref=run_ref)

    async def assemble(
        self, request: Mapping[str, Any], *, run_ref: str
    ) -> EngineAnswer:
        return await self._inner.assemble(request, run_ref=run_ref)

    async def log(
        self, op: str, body: Mapping[str, Any], *, run_ref: str
    ) -> EngineAnswer:
        return await self._inner.log(op, body, run_ref=run_ref)

    async def read(
        self, method: str, params: Mapping[str, Any], *, run_ref: str
    ) -> EngineAnswer:
        return await self._inner.read(method, params, run_ref=run_ref)

    async def write(
        self, method: str, params: Mapping[str, Any], *, run_ref: str
    ) -> EngineAnswer:
        return await self._inner.write(method, params, run_ref=run_ref)


def with_assistant_profile(
    port: EngineDecisionPort | None, metadata: Mapping[str, Any]
) -> EngineDecisionPort | None:
    """The run's port carrying the received profile ref; ``port`` when none was sent."""
    ref = metadata.get("assistant_profile_ref")
    if port is None or type(ref) is not str or not ref.strip():
        return port
    return ProfiledEnginePort(port, ref)


__all__ = [
    "ProfiledEnginePort",
    "decide",
    "with_assistant_profile",
    "digest_of",
    "read",
    "record",
    "write",
]
