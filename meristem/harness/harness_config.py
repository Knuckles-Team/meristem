"""Apply the sealed harness configuration the engine decided (MERISTEM-HARNESS-CYCLE-R015).

The harness reads three parts of one sealed answer, verifies its digest and
copies the values into ``HarnessConfig``. A part the answer marks abstained takes
its conservative default. The literals below are fallbacks for a missing answer only.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, replace
from typing import Any

from meristem.harness.engine_step import digest_of
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineAnswer

FALLBACK_SOFT = 0.6
FALLBACK_HARD = 0.85
FALLBACK_RECENT = 0.3
Q32 = 2**32


def _part(payload: Mapping[str, Any], name: str) -> Any:
    """One answer part; an abstained part yields its conservative default."""
    value = payload.get(name)
    if isinstance(value, Mapping) and value.get("abstained"):
        return value.get("conservative_default")
    return value


def _fraction(value: Any, fallback: float) -> float:
    return fallback if value is None else int(value) / Q32


@dataclass(frozen=True, slots=True)
class AppliedHarnessConfig:
    """The applied digest and the signed tool subset of one sealed answer."""

    digest: str
    tool_subset: tuple[str, ...] | None


def apply_harness_config(
    plan: Any, answer: EngineAnswer, *, harness_config_digest: str
) -> tuple[Any, AppliedHarnessConfig]:
    """Return the plan with the sealed configuration applied.

    The answer's digest must equal the envelope's ``harness_config_digest``, else
    the run stops ``HARNESS_DECISION_DIGEST_MISMATCH``. ``soft_threshold`` not
    below ``hard_threshold`` refuses ``harness_config_invalid``.
    """
    if answer.refusal_code is not None:
        raise ConsumerRefused(answer.refusal_code)
    payload = answer.payload
    if digest_of(dict(payload)) != harness_config_digest:
        raise ConsumerRefused("HARNESS_DECISION_DIGEST_MISMATCH")
    soft = _fraction(_part(payload, "soft_threshold_q32"), FALLBACK_SOFT)
    hard = _fraction(_part(payload, "hard_threshold_q32"), FALLBACK_HARD)
    recent = _fraction(_part(payload, "recent_window_q32"), FALLBACK_RECENT)
    if not soft < hard:
        raise ConsumerRefused("harness_config_invalid")
    space = _part(payload, "action_space")
    subset = space.get("tool_subset") if isinstance(space, Mapping) else None
    harness = replace(
        plan.harness,
        context_policy=_part(payload, "context_policy"),
        soft_threshold=soft,
        hard_threshold=hard,
        recent_window_fraction=recent,
        action_space=dict(space) if isinstance(space, Mapping) else None,
        plan_scaffold=_part(payload, "plan_scaffold") or plan.harness.plan_scaffold,
    )
    applied = AppliedHarnessConfig(
        harness_config_digest, tuple(subset) if subset is not None else None
    )
    return replace(plan, harness=harness), applied


def compaction_thresholds(harness: Any) -> tuple[float, float, float]:
    """The soft, hard and recent-window values the compaction step reads."""
    return (
        harness.soft_threshold if harness.soft_threshold is not None else FALLBACK_SOFT,
        harness.hard_threshold if harness.hard_threshold is not None else FALLBACK_HARD,
        harness.recent_window_fraction
        if harness.recent_window_fraction is not None
        else FALLBACK_RECENT,
    )


def check_tool_allowed(name: str, subset: Collection[str] | None) -> None:
    """Refuse a tool call outside the signed subset (R015.1.1, R054)."""
    if subset is not None and name not in subset:
        raise ConsumerRefused("TOOL_NOT_ALLOWED")


__all__ = [
    "AppliedHarnessConfig",
    "apply_harness_config",
    "check_tool_allowed",
    "compaction_thresholds",
]
