"""Observe actual in-run model requests without choosing or storing anything.

Client elapsed time includes transport and provider waiting; it is not provider
queue time, TTFT, or generation time. None of those are inferred here.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from time import perf_counter_ns
from typing import Any

from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter, ModelResponse
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings

MAX_REQUEST_SAMPLES = 128
MAX_SAFE_INTEGER = 9_007_199_254_740_991


def _elapsed_ms(started_ns: int) -> int:
    return max(0, (perf_counter_ns() - started_ns) // 1_000_000)


@dataclass(frozen=True, slots=True)
class StepUsage:
    """One model request's cost facts; cost itself is derived by the ledger."""

    run_id: str
    child_id: str | None
    operation: str
    model_ref: str | None
    tokens_in: int
    tokens_out: int
    tokens_cached: int
    latency_ms: int
    outcome: str
    content_digest: str

    def as_event(self) -> dict[str, Any]:
        """The ``step_usage`` run event; prompts and completions never enter it."""
        return {
            "type": "step_usage",
            "run_id": self.run_id,
            "child_id": self.child_id,
            "operation": self.operation,
            "model_ref": self.model_ref,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "tokens_cached": self.tokens_cached,
            "latency_ms": self.latency_ms,
            "outcome": self.outcome,
            "content_digest": self.content_digest,
        }


def _content_digest(
    messages: list[ModelMessage] | None, response: ModelResponse | None
) -> str:
    """SHA-256 of the canonical prompt and completion bytes."""
    parts = list(messages or [])
    if response is not None:
        parts.append(response)
    return hashlib.sha256(ModelMessagesTypeAdapter.dump_json(parts)).hexdigest()


@dataclass
class RunMetrics:
    """Bounded observations of this execution, never a cross-run accumulator."""

    started_ns: int = field(default_factory=perf_counter_ns)
    requests: list[dict[str, Any]] = field(default_factory=list)
    observed_requests: int = 0
    usage_sink: Callable[[dict[str, Any]], None] | None = None
    run_id: str = ""
    child_id: str | None = None
    operation: str = "run"

    def bind_usage(
        self,
        sink: Callable[[dict[str, Any]], None],
        *,
        run_id: str,
        child_id: str | None = None,
        operation: str = "run",
    ) -> None:
        """Send one ``step_usage`` event per model request to ``sink`` (R003.1)."""
        self.usage_sink, self.run_id = sink, run_id
        self.child_id, self.operation = child_id, operation

    def emit_step_usage(
        self,
        started_ns: int,
        response: ModelResponse | None,
        messages: list[ModelMessage] | None,
        model: WrapperModel,
    ) -> None:
        """Emit the step's usage event; a sink error never fails the run."""
        if self.usage_sink is None:
            return
        usage = response.usage if response is not None else None
        event = StepUsage(
            run_id=self.run_id,
            child_id=self.child_id,
            operation=self.operation,
            model_ref=(response.model_name if response else None) or model.model_name,
            tokens_in=int(getattr(usage, "input_tokens", 0) or 0),
            tokens_out=int(getattr(usage, "output_tokens", 0) or 0),
            tokens_cached=int(getattr(usage, "cache_read_tokens", 0) or 0),
            latency_ms=_elapsed_ms(started_ns),
            outcome="ok" if response is not None else "error",
            content_digest=_content_digest(messages, response),
        )
        try:
            self.usage_sink(event.as_event())
        except Exception:  # noqa: BLE001 - emission never fails the run
            return

    def record(
        self,
        started_ns: int,
        response: ModelResponse | None,
        *,
        model: WrapperModel,
        messages: list[ModelMessage] | None = None,
    ) -> None:
        self.observed_requests += 1
        self.emit_step_usage(started_ns, response, messages, model)
        if len(self.requests) >= MAX_REQUEST_SAMPLES:
            return
        sample: dict[str, Any] = {
            "sequence": self.observed_requests,
            "source": "client_monotonic_clock",
            "client_request_elapsed_ms": _elapsed_ms(started_ns),
            "outcome": "completed" if response is not None else "failed",
            "model_name": (response.model_name if response else None)
            or model.model_name,
            "provider_name": (response.provider_name if response else None)
            or model.system,
        }
        if response is not None:
            if response.provider_response_id:
                sample["provider_response_id"] = response.provider_response_id
            for name in ("input_tokens", "output_tokens"):
                value = getattr(response.usage, name, None)
                if type(value) is int and 0 <= value <= MAX_SAFE_INTEGER:
                    sample[name] = value
        self.requests.append(sample)

    def snapshot(self) -> dict[str, Any]:
        return {
            "source": "client_monotonic_clock",
            "run_elapsed_ms": _elapsed_ms(self.started_ns),
            "observed_model_requests": self.observed_requests,
            "coverage": (
                "complete"
                if len(self.requests) == self.observed_requests
                else "partial"
            ),
            "model_requests": [dict(sample) for sample in self.requests],
        }


class MeasuredModel(WrapperModel):
    """Measure the existing nonstreaming model call, preserving its semantics."""

    def __init__(self, wrapped: Any, metrics: RunMetrics):
        super().__init__(wrapped)
        self.metrics = metrics

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        started = perf_counter_ns()
        response: ModelResponse | None = None
        try:
            response = await self.wrapped.request(
                messages, model_settings, model_request_parameters
            )
            return response
        finally:
            self.metrics.record(started, response, model=self, messages=messages)
