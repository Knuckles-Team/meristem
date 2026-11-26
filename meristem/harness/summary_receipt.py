"""Invoke the admitted summary provider once and mint the receipt (MERISTEM-HARNESS-CYCLE-R068).

The receipt names the owner requirement, provider, model, policy revision and
the input and output digests; its ``receipt_digest`` is the SHA-256 of the
canonical other fields. Wrapper retries stay inside the one invocation. A
disabled provider refuses and mints no receipt.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from meristem.harness.engine_step import digest_of
from meristem.harness.plan import canonical_json
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import SummaryProvider, SummaryProviderDisabled


async def invoke_summary(
    provider: SummaryProvider | None,
    context: Mapping[str, Any],
    *,
    admission_ref: str,
    owner_requirement_id: str,
) -> tuple[str, dict[str, Any]]:
    """Return ``(output, receipt)`` for one provider invocation."""
    if provider is None:
        raise ConsumerRefused("SUMMARY_PROVIDER_UNAVAILABLE")
    try:
        result = await provider(context, admission_ref)
    except SummaryProviderDisabled as exc:
        raise ConsumerRefused("SUMMARY_PROVIDER_DISABLED") from exc
    fields: dict[str, Any] = {
        "owner_requirement_id": owner_requirement_id,
        "provider": result.provider,
        "model": result.model,
        "policy_revision": result.policy_revision,
        "input_context_digest": digest_of(dict(context)),
        "output_digest": hashlib.sha256(result.output.encode("utf-8")).hexdigest(),
    }
    fields["receipt_id"] = digest_of({"admission": admission_ref, **fields})[:32]
    receipt_digest = hashlib.sha256(canonical_json(fields).encode("utf-8")).hexdigest()
    return result.output, {**fields, "receipt_digest": receipt_digest}


__all__ = ["invoke_summary"]
