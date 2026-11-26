"""Report the first replayed step digest that differs
(MERISTEM-HARNESS-CYCLE-R072)."""

from __future__ import annotations

import pytest

from meristem.harness.replay import first_divergence, step_digest

# spec: MERISTEM-HARNESS-CYCLE-R072

STEPS = [{"i": 0}, {"i": 1}, {"i": 2}]


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R072")
def test_first_divergence_is_reported():
    stored = [step_digest(STEPS[0]), step_digest({"i": 9}), step_digest(STEPS[2])]
    assert first_divergence(STEPS, stored) == 1


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R072")
def test_equal_and_length_difference():
    stored = [step_digest(s) for s in STEPS]
    assert first_divergence(STEPS, stored) is None
    assert first_divergence(STEPS, stored[:2]) == 2
    assert step_digest({"b": 1, "a": 2}) == step_digest({"a": 2, "b": 1})
