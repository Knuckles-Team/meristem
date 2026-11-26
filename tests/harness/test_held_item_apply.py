"""A held item applies only after an admitted verdict."""

from __future__ import annotations

import pytest

from meristem.harness.held_item import HeldItemRefused, apply_held_item
from meristem.host_ports import ConfirmVerdict

SPEC = "MERISTEM-HARNESS-CYCLE-R056"


async def run_item(verdict):
    applied = []

    async def confirm(kind, payload, run_ref):
        assert kind == "held_item" and payload["item_ref"] == "item-1"
        if isinstance(verdict, Exception):
            raise verdict
        return verdict

    async def apply():
        applied.append(True)

    outcome = await apply_held_item(
        confirm, apply, run_ref="r1", item_ref="item-1", item_kind="program"
    )
    return outcome, applied


@pytest.mark.spec(SPEC)
async def test_refused_item_is_not_applied():
    outcome, applied = await run_item(ConfirmVerdict("admitted", ref="v-1"))
    assert outcome.applied and applied == [True] and outcome.verdict_ref == "v-1"
    for verdict in (ConfirmVerdict("rejected", ref="v-2"), ConfirmVerdict("abstained")):
        outcome, applied = await run_item(verdict)
        assert not outcome.applied and applied == []
    for verdict in (
        ConfirmVerdict("admitted", refusal_code="HeldItemRefusal"),
        RuntimeError("down"),
    ):
        applied = []
        with pytest.raises(HeldItemRefused):
            await run_item(verdict)
        assert applied == []
    with pytest.raises(HeldItemRefused):
        await apply_held_item(
            None, lambda: None, run_ref="r", item_ref="i", item_kind="program"
        )
