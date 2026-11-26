"""Disclosed instruction sets: applied as received, bound by a receipt.

The caller submits the phase and trigger kinds; the engine versions and hashes
the set (MERISTEM-HARNESS-CYCLE-R017.6, R045.1). The harness applies the bytes it
received without substitution and keeps the applied version and SHA-256 in a
receipt that travels with the next decision-log write.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from meristem.harness import engine_step
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineDecisionPort

KIND = "instruction_disclosure"


@dataclass(frozen=True, slots=True)
class Disclosure:
    """One applied disclosure: the exact text, its versioned id and its event."""

    text: str
    applied: str
    event: dict[str, Any]
    receipt: dict[str, str]


async def disclose_instructions(
    run_id: str,
    phase: str,
    triggers: Sequence[str],
    port: EngineDecisionPort | None,
    *,
    sequence: int,
    acted: bool = False,
) -> Disclosure:
    """Ask one disclosure and apply the returned set byte for byte.

    The ``initial`` phase runs once before the first action; after an action it
    refuses ``disclosure_after_action``. A returned hash that differs from the
    applied bytes refuses ``disclosure_substituted``. Engine codes such as
    ``DISCLOSURE_VERSION_STALE`` and ``DISCLOSURE_UNAUTHORIZED`` pass through.
    """
    if phase == "initial" and acted:
        raise ConsumerRefused("disclosure_after_action")
    answer = await engine_step.decide(
        port,
        KIND,
        {
            "phase": phase,
            "run_id": run_id,
            "sequence": sequence,
            "occurred_triggers": list(triggers),
        },
        run_ref=run_id,
    )
    payload = answer.payload
    text = str(payload.get("text", ""))
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if payload.get("hash") != digest:
        raise ConsumerRefused("disclosure_substituted")
    applied = f"{payload.get('set_id')}:{payload.get('version')}:{digest}"
    event = {
        "type": "instruction_disclosed",
        "phase": phase,
        "trigger": list(triggers),
        "version": payload.get("version"),
        "digest": digest,
        "run_id": run_id,
        "sequence": sequence,
        "outcome": "applied",
    }
    receipt = {"version": str(payload.get("version")), "hash": digest}
    return Disclosure(text, applied, event, receipt)


async def send_receipt(
    port: EngineDecisionPort | None, run_id: str, disclosure: Disclosure
) -> None:
    """Send the disclosure receipt through the decision-log write."""
    await engine_step.record(
        port, "DisclosureReceipt", disclosure.receipt, run_ref=run_id
    )


__all__ = ["Disclosure", "disclose_instructions", "send_receipt"]
