"""Treat the replay event source as an external producer (MERISTEM-RUN-R006.1).

The envelope is exactly ``ReplayEvent{event_id, seq, event_time_ms, snapshot,
action_id, outcome_refs, source_revision}``. Unknown or missing fields are
rejected, sequence numbers rise by one, an identical duplicate is idempotent and a
conflicting one is refused. The harness produces no replay events.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from meristem.harness.run_lifecycle import ConsumerRefused

FIELDS = frozenset(
    {
        "event_id",
        "seq",
        "event_time_ms",
        "snapshot",
        "action_id",
        "outcome_refs",
        "source_revision",
    }
)


def admit_replay_events(
    events: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    """Validate the envelopes and return them deduplicated, in sequence order."""
    admitted: list[Mapping[str, Any]] = []
    by_id: dict[str, Mapping[str, Any]] = {}
    for event in events:
        if not isinstance(event, Mapping) or set(event) != FIELDS:
            raise ConsumerRefused("REPLAY_ENVELOPE_MALFORMED")
        if type(event["seq"]) is not int or type(event["event_id"]) is not str:
            raise ConsumerRefused("REPLAY_ENVELOPE_MALFORMED")
        earlier = by_id.get(event["event_id"])
        if earlier is not None:
            if dict(earlier) != dict(event):
                raise ConsumerRefused("REPLAY_DUPLICATE_CONFLICT")
            continue
        if admitted:
            last = admitted[-1]["seq"]
            if event["seq"] <= last:
                raise ConsumerRefused("REPLAY_OUT_OF_ORDER")
            if event["seq"] != last + 1:
                raise ConsumerRefused("REPLAY_SEQUENCE_GAP")
        by_id[event["event_id"]] = event
        admitted.append(event)
    return tuple(admitted)


__all__ = ["FIELDS", "admit_replay_events"]
