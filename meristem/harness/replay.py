"""Compare replayed step digests with the stored digests.

The stored digests arrive from the host; the harness computes each replayed
digest and reports the first index that differs. It decides nothing.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any


def step_digest(step: Mapping[str, Any]) -> str:
    """SHA-256 of one step as canonical JSON, 64 lowercase hex."""
    canonical = json.dumps(
        step,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def first_divergence(
    steps: Sequence[Mapping[str, Any]], stored: Sequence[str]
) -> int | None:
    """The first index where the replayed digest differs, else ``None``.

    A length difference diverges at the first index past the shorter side.
    """
    for index, (step, digest) in enumerate(zip(steps, stored, strict=False)):
        if step_digest(step) != digest:
            return index
    if len(steps) != len(stored):
        return min(len(steps), len(stored))
    return None
