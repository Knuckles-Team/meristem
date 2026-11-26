"""Skill invocation events and the session-cost delta (R091, R092).

The harness reports facts about one skill span. It decides nothing: it keeps
no prompt, argument or output text, and sends the event to the engine through
the received write port.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

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
        "skill_ref",
        "skill_version",
        "chosen_by",
        "run_id",
        "started_ms",
        "ended_ms",
        "outcome",
        "error_code",
        "cost_delta_micro_usd",
    }
)
FIELD_BLOCKED = "SKILL_EVENT_FIELD_BLOCKED"
#: The served engine write method that ingests one skill event.
INGEST_METHOD = "SkillTelemetryIngest"


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

    skill_ref: str
    skill_version: str
    chosen_by: str
    run_id: str
    started_ms: int
    ended_ms: int | None = None
    outcome: str | None = None
    error_code: str | None = None
    cost_delta_micro_usd: int | None = None

    def as_payload(self) -> dict[str, Any]:
        """The wire shape: exactly the closed field list."""
        return {name: getattr(self, name) for name in sorted(EVENT_FIELDS)}


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
    ``ingest`` sends one payload to the engine (the host binds it to the
    ``SkillTelemetryIngest`` write); ``span`` exports the same payload as a
    trace span. With no ``price`` the cost delta is ``None``, never zero.
    """

    run_id: str
    read_usage: Callable[[], tuple[int, int]]
    ingest: Callable[[str, Mapping[str, Any]], Awaitable[Any]]
    span: Callable[[str, Mapping[str, Any]], None] = lambda kind, payload: None
    price: PriceVersion | None = None
    clock_ms: Callable[[], int] = lambda: 0
    blocked: int = 0
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
        raw = {
            "skill_ref": skill_ref,
            "skill_version": skill_version,
            "chosen_by": chosen_by,
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
        await self._send("skill.start", event)
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
        }
        event = sanitize(raw)
        if event is None:
            self.blocked += 1
            return None
        await self._send("skill.end", event)
        return event

    def attach(self, lifecycle: Any) -> None:
        """Register both hooks on a run lifecycle under the name ``skill_events``."""

        async def start(span: Mapping[str, Any]) -> None:
            await self.on_skill_start(**span)

        async def end(span: Mapping[str, Any]) -> None:
            await self.on_skill_end(**span)

        lifecycle.add("skill_events", "on_skill_start", start)
        lifecycle.add("skill_events", "on_skill_end", end)

    async def _send(self, kind: str, event: SkillInvocationEvent) -> None:
        payload = event.as_payload()
        self.span(kind, payload)
        await self.ingest(INGEST_METHOD, payload)


__all__ = [
    "ERROR_CODES",
    "FIELD_BLOCKED",
    "INGEST_METHOD",
    "PriceVersion",
    "SkillEventRecorder",
    "SkillInvocationEvent",
    "sanitize",
]
