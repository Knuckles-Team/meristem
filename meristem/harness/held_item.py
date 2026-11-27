"""Apply a held item only after an admitted verdict (MERISTEM-HARNESS-CYCLE-R056).

A run output proposal or a stored program waits for one ``arbora.plan/confirm``
request with kind ``held_item``. The item applies only on ``admitted``; a
rejected or abstained verdict or any refusal leaves it held. The step decides
nothing.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import PlanConfirmer


@dataclass(frozen=True)
class HeldOutputConfig:
    """The received refs that make one generated output a held output (R101).

    The harness derives only the content digest of the exact output; every
    other value is received from the host and the engine and carried as is.
    ``encode`` turns the output text into the exact bytes the host digests
    (default: UTF-8 of the text).
    """

    item_ref: str
    engine_decision_ref: str
    checklist: tuple[tuple[str, str], ...]
    verifier: str | None = None
    compiler: str | None = None
    verification_ref: str | None = None
    encode: Callable[[str], bytes] | None = None


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


def held_output_block(output: str, config: HeldOutputConfig) -> Mapping[str, Any]:
    """The held-output block of one generated output (MERISTEM-HARNESS-CYCLE-R101).

    The keys match the held output the host critique gate reads: ``item_ref``,
    ``content_digest``, ``engine_decision_ref`` and ``checklist`` (a list of
    ``{id, question}``). Only ``content_digest`` is derived here, as the
    SHA-256 of the exact output bytes. The received verification record
    (``verifier``, ``compiler``, ``verification_ref``) travels beside the
    block under ``verification``, only when received. The step decides nothing.
    """
    raw = config.encode(output) if config.encode else output.encode("utf-8")
    block: dict[str, Any] = {
        "item_ref": config.item_ref,
        "content_digest": hashlib.sha256(raw).hexdigest(),
        "engine_decision_ref": config.engine_decision_ref,
        "checklist": [{"id": i, "question": q} for i, q in config.checklist],
    }
    verification = {
        key: value
        for key, value in (
            ("verifier", config.verifier),
            ("compiler", config.compiler),
            ("verification_ref", config.verification_ref),
        )
        if value is not None
    }
    if verification:
        block["verification"] = verification
    return block


__all__ = [
    "HeldItemRefused",
    "HeldItemResult",
    "apply_held_item",
    "held_output_block",
]
