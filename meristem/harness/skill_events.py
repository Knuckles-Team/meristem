"""Skill invocation events and the session-cost delta (R091, R092).

The harness reports facts about one skill span. It decides nothing: it keeps
no prompt, argument or output text, and sends the event to the engine through
the received write port.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from meristem.host_ports import EngineDecisionRefused

#: The closed error codes a skill event may carry.
ERROR_CODES: frozenset[str] = frozenset(
    {
        "SKILL_NOT_FOUND",
        "SKILL_LOAD_FAILED",
        "SKILL_TOOL_FAILED",
        "SKILL_TIMEOUT",
        "SKILL_CANCELLED",
    }
)
OUTCOMES: frozenset[str] = frozenset({"ok", "error", "cancelled"})
CHOSEN_BY: frozenset[str] = frozenset({"agent", "user"})
EVENT_FIELDS: frozenset[str] = frozenset(
    {
        "invocation_id",
        "tenant_id",
        "skill_ref",
        "skill_version",
        "chosen_by",
        "principal_pseudonym",
        "run_id",
        "started_ms",
        "ended_ms",
        "outcome",
        "error_code",
        "cost_delta_micro_usd",
        "tokens_in",
        "tokens_out",
    }
)
FIELD_BLOCKED = "SKILL_EVENT_FIELD_BLOCKED"
#: The served engine method that ingests skill invocations (op ``ingest``).
INGEST_METHOD = "SkillTelemetry"
INGEST_OP = "ingest"
#: The outcome a cancelled span maps to: the engine knows only ``ok`` and
#: ``error`` with a closed upper-case code.
CANCELLED_CODE = "SKILL_CANCELLED"
ENGINE_UNAVAILABLE = "ENGINE_METHOD_UNAVAILABLE"


async def send_engine(
    send: Callable[[str, Mapping[str, Any]], Awaitable[Any]],
    method: str,
    payload: Mapping[str, Any],
) -> bool:
    """Send one engine call; ``False`` when the pinned engine cannot serve it.

    A method the released engine lacks, a refused or forbidden method, an
    unbound port and a transport failure (connection or timeout) all surface
    as unavailable. The caller counts it and goes on. Any other error,
    including ``AttributeError`` and ``LookupError``, is a programming error
    and propagates.
    """
    try:
        await send(method, payload)
    except (
        EngineDecisionRefused,
        NotImplementedError,
        ConnectionError,
        TimeoutError,
    ):
        return False
    return True


@dataclass(frozen=True, slots=True)
class PriceVersion:
    """Received per-million-token prices in micro USD, with their version."""

    version: str
    input_micro_usd_per_mtok: int
    output_micro_usd_per_mtok: int

    def cost(self, input_tokens: int, output_tokens: int) -> int:
        """Whole micro USD for the given token counts."""
        total = (
            input_tokens * self.input_micro_usd_per_mtok
            + output_tokens * self.output_micro_usd_per_mtok
        )
        return total // 1_000_000


@dataclass(frozen=True, slots=True)
class SkillInvocationEvent:
    """One start or end fact of a skill span; it holds no text of the run."""

    invocation_id: str
    tenant_id: str
    skill_ref: str
    skill_version: str
    chosen_by: str
    principal_pseudonym: str
    run_id: str
    started_ms: int
    ended_ms: int | None = None
    outcome: str | None = None
    error_code: str | None = None
    cost_delta_micro_usd: int | None = None
    tokens_in: int = 0
    tokens_out: int = 0

    def as_payload(self) -> dict[str, Any]:
        """The span shape: exactly the closed field list."""
        return {name: getattr(self, name) for name in sorted(EVENT_FIELDS)}

    def as_invocation(self) -> dict[str, Any]:
        """The engine ``SkillInvocation`` shape of a finished span.

        ``outcome`` is ``"ok"`` or ``{"error": {"code": ...}}``; a cancelled
        span is an error with ``SKILL_CANCELLED``. The engine overwrites
        ``tenant_id`` with the verified tenant.
        """
        outcome: Any
        if self.outcome == "ok":
            outcome = "ok"
        else:
            code = self.error_code or CANCELLED_CODE
            outcome = {"error": {"code": code}}
        return {
            "invocation_id": self.invocation_id,
            "tenant_id": self.tenant_id,
            "skill_ref": self.skill_ref,
            "skill_version": self.skill_version,
            "chosen_by": self.chosen_by,
            "principal_pseudonym": self.principal_pseudonym,
            "run_id": self.run_id,
            "started_ms": self.started_ms,
            "ended_ms": self.ended_ms,
            "outcome": outcome,
            "cost_delta_micro_usd": self.cost_delta_micro_usd,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
        }


def sanitize(raw: Mapping[str, Any]) -> SkillInvocationEvent | None:
    """Build an event from a raw mapping, or ``None`` when a field is blocked."""
    if set(raw) - EVENT_FIELDS:
        return None
    event = SkillInvocationEvent(**raw)
    if event.chosen_by not in CHOSEN_BY:
        return None
    if event.outcome is not None and event.outcome not in OUTCOMES:
        return None
    if event.error_code is not None and event.error_code not in ERROR_CODES:
        return None
    if event.outcome == "error" and event.error_code is None:
        return None
    return event


@dataclass(slots=True)
class _Span:
    event: SkillInvocationEvent
    input_tokens: int
    output_tokens: int
    child_input: int = 0
    child_output: int = 0


@dataclass
class SkillEventRecorder:
    """Run-scoped recorder behind the ``on_skill_start`` and ``on_skill_end`` hooks.

    ``read_usage`` returns ``(input_tokens, output_tokens)`` of the run.
    ``ingest`` sends one call to the engine (the host binds it to the
    ``SkillTelemetry`` write). The engine takes finished invocations only, so
    the start event is a span and the end event is the one ``ingest`` op with a
    ``SkillInvocation`` of the closed shape. ``principal_pseudonym`` is the
    host's ``skill_pseudonym`` of the caller. With no ``price`` the cost delta
    is ``None``, never zero. ``unavailable`` counts ingests the pinned engine
    could not serve.
    """

    run_id: str
    read_usage: Callable[[], tuple[int, int]]
    ingest: Callable[[str, Mapping[str, Any]], Awaitable[Any]]
    span: Callable[[str, Mapping[str, Any]], None] = lambda kind, payload: None
    price: PriceVersion | None = None
    clock_ms: Callable[[], int] = lambda: 0
    tenant_id: str = ""
    principal_pseudonym: str = ""
    blocked: int = 0
    unavailable: int = 0
    _seq: int = 0
    _open: list[_Span] = field(default_factory=list)

    async def on_skill_start(
        self,
        skill_ref: str,
        skill_version: str,
        *,
        chosen_by: str = "agent",
        **extra: Any,
    ) -> bool:
        """Open a span and emit the start event; ``False`` when blocked."""
        self._seq += 1
        raw = {
            "invocation_id": f"{self.run_id}:{self._seq}",
            "tenant_id": self.tenant_id,
            "skill_ref": skill_ref,
            "skill_version": skill_version,
            "chosen_by": chosen_by,
            "principal_pseudonym": self.principal_pseudonym,
            "run_id": self.run_id,
            "started_ms": self.clock_ms(),
            **extra,
        }
        event = sanitize(raw)
        if event is None:
            self.blocked += 1
            return False
        used = self.read_usage()
        self._open.append(_Span(event, used[0], used[1]))
        self.span("skill.start", event.as_payload())
        return True

    async def on_skill_end(
        self, *, outcome: str = "ok", error_code: str | None = None
    ) -> SkillInvocationEvent | None:
        """Close the innermost span and emit the end event with its cost delta."""
        if not self._open:
            return None
        span = self._open.pop()
        used_in, used_out = self.read_usage()
        d_in = used_in - span.input_tokens
        d_out = used_out - span.output_tokens
        cost: int | None = None
        if self.price is not None:
            cost = self.price.cost(d_in - span.child_input, d_out - span.child_output)
        if self._open:
            parent = self._open[-1]
            parent.child_input += d_in
            parent.child_output += d_out
        raw = {
            **{k: getattr(span.event, k) for k in EVENT_FIELDS},
            "ended_ms": self.clock_ms(),
            "outcome": outcome,
            "error_code": error_code,
            "cost_delta_micro_usd": cost,
            "tokens_in": d_in,
            "tokens_out": d_out,
        }
        event = sanitize(raw)
        if event is None:
            self.blocked += 1
            return None
        self.span("skill.end", event.as_payload())
        sent = await send_engine(
            self.ingest,
            INGEST_METHOD,
            {"op": INGEST_OP, "events": [event.as_invocation()]},
        )
        if not sent:
            self.unavailable += 1
        return event

    def attach(self, lifecycle: Any) -> None:
        """Register both hooks on a run lifecycle under the name ``skill_events``."""

        async def start(span: Mapping[str, Any]) -> None:
            await self.on_skill_start(**span)

        async def end(span: Mapping[str, Any]) -> None:
            await self.on_skill_end(**span)

        lifecycle.add("skill_events", "on_skill_start", start)
        lifecycle.add("skill_events", "on_skill_end", end)


__all__ = [
    "ERROR_CODES",
    "FIELD_BLOCKED",
    "CANCELLED_CODE",
    "ENGINE_UNAVAILABLE",
    "INGEST_METHOD",
    "INGEST_OP",
    "PriceVersion",
    "SkillEventRecorder",
    "SkillInvocationEvent",
    "sanitize",
    "send_engine",
]
