"""Apply a held item only after an admitted verdict (MERISTEM-HARNESS-CYCLE-R056).

A run output proposal or a stored program waits for one ``arbora.plan/confirm``
request with kind ``held_item``. The item applies only on ``admitted``; a
rejected or abstained verdict or any refusal leaves it held. The step decides
nothing.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import PlanConfirmer


class HeldItemRefused(ConsumerRefused):
    """The host refused or failed the held-item confirmation."""


@dataclass(frozen=True, slots=True)
class HeldItemResult:
    """Whether the item applied, with the verdict reference for the trace."""

    applied: bool
    verdict: str
    verdict_ref: str | None


async def apply_held_item(
    confirm: PlanConfirmer | None,
    apply: Callable[[], Awaitable[Any]],
    *,
    run_ref: str,
    item_ref: str,
    item_kind: str,
) -> HeldItemResult:
    """Confirm the held item and apply it only on an ``admitted`` verdict."""
    if confirm is None:
        raise HeldItemRefused("HELD_ITEM_CONFIRM_UNAVAILABLE")
    try:
        answer = await confirm(
            "held_item",
            {
                "confirmation_kind": "held_item",
                "item_ref": item_ref,
                "item_kind": item_kind,
            },
            run_ref,
        )
    except Exception as exc:  # noqa: BLE001 - a failed confirmation leaves it held
        raise HeldItemRefused("HELD_ITEM_CONFIRM_FAILED") from exc
    if answer.refusal_code is not None:
        raise HeldItemRefused(answer.refusal_code)
    if answer.verdict != "admitted":
        return HeldItemResult(False, answer.verdict, answer.ref)
    await apply()
    return HeldItemResult(True, answer.verdict, answer.ref)


__all__ = ["HeldItemRefused", "HeldItemResult", "apply_held_item"]
