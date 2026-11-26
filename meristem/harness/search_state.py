"""Search state across continuation and compaction.

The harness owns the item schemas of the four fields (``transcript``,
``evidence``, ``constraints``, ``open_claims``). It serializes them as one
canonical JSON document, saves it through ``SyncMemorySnapshot`` and restores
it through ``ReadMemorySnapshot``. The engine holds the document; the harness
keeps no copy and decides nothing.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineDecisionPort, EngineDecisionRefused

FIELDS = ("transcript", "evidence", "constraints", "open_claims")
DOC_KIND = "search_state"


@dataclass(frozen=True, slots=True)
class SearchStateFields:
    """The four restored fields, each a list of items."""

    transcript: tuple[Any, ...]
    evidence: tuple[Any, ...]
    constraints: tuple[Any, ...]
    open_claims: tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class SavedSearchState:
    """The engine's receipt of one save."""

    doc_id: str
    revision: int
    digests: Mapping[str, str]


def _canonical(value: Any) -> str:
    """Compact JSON with sorted keys and raw UTF-8: the engine's digest recipe."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def field_digest(items: Sequence[Any]) -> str:
    """SHA-256 of one field's items as canonical JSON."""
    return hashlib.sha256(_canonical(list(items)).encode("utf-8")).hexdigest()


def _port(port: EngineDecisionPort | None) -> EngineDecisionPort:
    if port is None:
        raise EngineDecisionRefused("engine_decision_unavailable")
    return port


async def save_search_state(
    state: SearchStateFields,
    port: EngineDecisionPort | None,
    *,
    search_run_id: str,
    doc_id: str,
    previous_revision: int,
    request_id: str,
    run_ref: str,
) -> SavedSearchState:
    """Save one revision (the previous revision plus one).

    A ``conflict_retained`` outcome stops the save and re-reads the latest
    revision; the caller receives ``SEARCH_STATE_REVISION_MISMATCH``. Engine
    ``SEARCH_STATE_*`` codes pass through. The write is the engine's
    ``SyncMemorySnapshot`` ``submit`` action with the content digest and the
    expected head revision (none for the first revision).
    """
    bound = _port(port)
    revision = previous_revision + 1
    digests = {name: field_digest(getattr(state, name)) for name in FIELDS}
    document = _canonical(
        {
            "search_run_id": search_run_id,
            "state_revision": revision,
            **{name: list(getattr(state, name)) for name in FIELDS},
        }
    )
    # The engine derives the four digests on read with the same recipe.
    answer = await bound.write(
        "SyncMemorySnapshot",
        {
            "request": {
                "action": {
                    "action": "submit",
                    "content": document,
                    "content_digest": hashlib.sha256(document.encode()).hexdigest(),
                    "expected_revision": previous_revision or None,
                },
                "doc_id": doc_id,
                "doc_kind": DOC_KIND,
                "request_id": request_id,
            },
        },
        run_ref=run_ref,
    )
    if answer.refusal_code is not None:
        raise ConsumerRefused(answer.refusal_code)
    if answer.payload.get("outcome") == "conflict_retained":
        await load_search_state(bound, doc_id=doc_id, run_ref=run_ref)
        raise ConsumerRefused("SEARCH_STATE_REVISION_MISMATCH")
    head = answer.payload.get("head")
    head_revision = head.get("revision") if isinstance(head, Mapping) else None
    if type(head_revision) is not int:
        raise ConsumerRefused("SEARCH_STATE_MALFORMED")
    return SavedSearchState(doc_id=doc_id, revision=head_revision, digests=digests)


async def load_search_state(
    port: EngineDecisionPort | None,
    *,
    doc_id: str,
    revision: int | None = None,
    run_ref: str,
) -> Mapping[str, Any]:
    """Read one snapshot; continuation passes the document id and revision."""
    params: dict[str, Any] = {"doc_id": doc_id}
    if revision is not None:
        params["revision"] = revision
    answer = await _port(port).read("ReadMemorySnapshot", params, run_ref=run_ref)
    if answer.refusal_code is not None:
        raise ConsumerRefused(answer.refusal_code)
    return answer.payload


async def restore_fields(
    port: EngineDecisionPort | None,
    *,
    doc_id: str,
    revision: int | None = None,
    run_ref: str,
) -> SearchStateFields:
    """Restore all four fields; a compacted summary may be used only after.

    Every field must be present and its digest must equal the engine's
    ``search_state`` evidence digest. A missing or mismatched field refuses
    ``search_state_incomplete``.
    """
    snapshot = await load_search_state(
        port, doc_id=doc_id, revision=revision, run_ref=run_ref
    )
    try:
        document = json.loads(str(snapshot.get("content", "")))
    except ValueError:
        raise ConsumerRefused("search_state_incomplete") from None
    evidence = snapshot.get("search_state")
    if not isinstance(document, Mapping) or not isinstance(evidence, Mapping):
        raise ConsumerRefused("search_state_incomplete")
    restored: dict[str, tuple[Any, ...]] = {}
    for name in FIELDS:
        items = document.get(name)
        if not isinstance(items, list):
            raise ConsumerRefused("search_state_incomplete")
        if field_digest(items) != evidence.get(f"{name}_digest"):
            raise ConsumerRefused("search_state_incomplete")
        restored[name] = tuple(items)
    return SearchStateFields(**restored)
