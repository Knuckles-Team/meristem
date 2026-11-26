"""Ask the host to admit a risky tool action; every refusal denies (MERISTEM-HARNESS-CYCLE-R055).

A tool of class ``egress``, ``privileged_call`` or ``delegation`` that runs after
untrusted content sends one ``arbora.plan/confirm`` request with kind
``tool_action``. The guard applies an ``admit`` verdict and denies on ``deny``,
on any refusal and on a missing answer. It decides nothing and holds no threshold.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import PlanConfirmer


class ToolActionRefused(ConsumerRefused):
    """The host denied or refused the action; the tool step maps it to ToolCallRefused."""


GUARDED_CLASSES = frozenset({"egress", "privileged_call", "delegation"})


@dataclass(frozen=True, slots=True)
class ToolActionAdmission:
    """The trace record of one admitted action."""

    approval_ref: str | None
    decision_ref: str | None


async def admit_tool_action(
    confirm: PlanConfirmer | None,
    *,
    run_ref: str,
    tool: str,
    tool_class: str,
    after_untrusted: bool,
    arguments_digest: str,
) -> ToolActionAdmission | None:
    """Return the admission record, or ``None`` when no confirmation applies.

    Raises :class:`ToolActionRefused` when the host denies, refuses or is absent.
    """
    if tool_class not in GUARDED_CLASSES or not after_untrusted:
        return None
    if confirm is None:
        raise ToolActionRefused("TOOL_ACTION_CONFIRM_UNAVAILABLE")
    payload: Mapping[str, Any] = {
        "confirmation_kind": "tool_action",
        "tool": tool,
        "tool_class": tool_class,
        "arguments_digest": arguments_digest,
    }
    try:
        answer = await confirm("tool_action", payload, run_ref)
    except Exception as exc:  # noqa: BLE001 - any refusal is a deny
        raise ToolActionRefused("TOOL_ACTION_DENIED") from exc
    if answer.refusal_code is not None or answer.verdict != "admit":
        raise ToolActionRefused(answer.refusal_code or "TOOL_ACTION_DENIED")
    return ToolActionAdmission(approval_ref=answer.ref, decision_ref=answer.ref)


__all__ = [
    "GUARDED_CLASSES",
    "ToolActionAdmission",
    "ToolActionRefused",
    "admit_tool_action",
]
