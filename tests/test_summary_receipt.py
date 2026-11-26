"""One summary invocation mints one receipt; a disabled provider mints none."""

from __future__ import annotations

import hashlib

import pytest

from meristem.harness.plan import canonical_json
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.harness.summary_receipt import invoke_summary
from meristem.host_ports import SummaryProviderDisabled, SummaryResult

CONTEXT = {"records": ["a", "b"]}


async def test_one_invocation_one_receipt():
    calls = []

    async def provider(context, admission_ref):
        calls.append(admission_ref)
        return SummaryResult("the summary", "prov", "model-1", "rev-3")

    output, receipt = await invoke_summary(
        provider, CONTEXT, admission_ref="adm-1", owner_requirement_id="ARBORA-OPS-R065"
    )
    assert output == "the summary" and calls == ["adm-1"]
    assert set(receipt) == {
        "owner_requirement_id",
        "receipt_id",
        "receipt_digest",
        "provider",
        "model",
        "policy_revision",
        "input_context_digest",
        "output_digest",
    }
    other = {k: v for k, v in receipt.items() if k != "receipt_digest"}
    assert (
        receipt["receipt_digest"]
        == hashlib.sha256(canonical_json(other).encode()).hexdigest()
    )
    assert receipt["output_digest"] == hashlib.sha256(b"the summary").hexdigest()

    async def disabled(context, admission_ref):
        raise SummaryProviderDisabled

    with pytest.raises(ConsumerRefused, match="SUMMARY_PROVIDER_DISABLED"):
        await invoke_summary(
            disabled, CONTEXT, admission_ref="adm-1", owner_requirement_id="X"
        )
    with pytest.raises(ConsumerRefused, match="SUMMARY_PROVIDER_UNAVAILABLE"):
        await invoke_summary(None, CONTEXT, admission_ref="a", owner_requirement_id="X")
