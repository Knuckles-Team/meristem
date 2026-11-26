"""Verify the received input projection before the worker call
(MERISTEM-HARNESS-CYCLE-R069)."""

from __future__ import annotations

import hashlib
import json

import pytest

from meristem.harness import plan_runtime
from meristem.harness.run_lifecycle import ConsumerRefused

SPEC = "MERISTEM-HARNESS-CYCLE-R069"
# spec: MERISTEM-HARNESS-CYCLE-R069


def _digest(value: object) -> str:
    canonical = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


PAYLOAD = {"input": "data", "other": "x"}
TASK = {"task_id": "t1"}


@pytest.mark.spec(SPEC)
def test_worker_gets_the_projected_payload_only():
    projection = {"payload": PAYLOAD, "digest": _digest(PAYLOAD)}

    payload = plan_runtime.apply_input_projection(TASK, projection)

    # The worker gets exactly the projected payload it received, nothing more.
    assert payload == PAYLOAD


@pytest.mark.spec(SPEC)
def test_digest_mismatch_refuses_input_projection_digest_mismatch():
    projection = {"payload": PAYLOAD, "digest": "0" * 64}

    with pytest.raises(ConsumerRefused, match="input_projection_digest_mismatch"):
        plan_runtime.apply_input_projection(TASK, projection)


@pytest.mark.spec(SPEC)
def test_projection_outside_the_closed_shape_refuses():
    with pytest.raises(ConsumerRefused, match="input_projection_invalid"):
        plan_runtime.apply_input_projection(TASK, ["not", "a", "mapping"])
    with pytest.raises(ConsumerRefused, match="input_projection_invalid"):
        plan_runtime.apply_input_projection(TASK, {"payload": "raw"})
