"""Admit graph-referenced and graph-ingested study sources (MERISTEM-RUN-R003, R003.1).

A study source is a filesystem source (``path``, ``digest``) or a graph source
(``tenant_id``, ``graph``, ``node``, ``revision``, ``content_digest``). The
harness holds no source store: a graph body is read through the host content
port and refused when its SHA-256 differs from the received digest.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import StudyContentReader

STUDY_SOURCE_KINDS = frozenset({"filesystem", "graph_ref", "graph_ingested"})
GRAPH_FIELDS = ("tenant_id", "graph", "node", "revision", "content_digest")
_HEX64 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class FilesystemSource:
    path: str
    digest: str


@dataclass(frozen=True, slots=True)
class GraphRefSource:
    tenant_id: str
    graph: str
    node: str
    revision: int
    content_digest: str


StudySource = FilesystemSource | GraphRefSource


async def admit_study_sources(
    sources: Sequence[Mapping[str, Any]], reader: StudyContentReader | None
) -> tuple[StudySource, ...]:
    """Admit every received source or refuse with the first typed code."""
    admitted: list[StudySource] = []
    for source in sources:
        if source.get("kind") == "filesystem":
            if not source.get("path") or not source.get("digest"):
                raise ConsumerRefused("STUDY_SOURCE_FIELD_MISSING")
            admitted.append(FilesystemSource(source["path"], source["digest"]))
            continue
        if any(source.get(name) in (None, "") for name in GRAPH_FIELDS):
            raise ConsumerRefused("STUDY_SOURCE_FIELD_MISSING")
        digest = source["content_digest"]
        if not isinstance(digest, str) or not _HEX64.fullmatch(digest):
            raise ConsumerRefused("STUDY_SOURCE_FIELD_MISSING")
        if reader is None:
            raise ConsumerRefused("STUDY_CONTENT_UNAVAILABLE")
        body = await reader(
            source["tenant_id"], source["graph"], source["node"], source["revision"]
        )
        if hashlib.sha256(body).hexdigest() != digest:
            raise ConsumerRefused("STUDY_SOURCE_DIGEST_MISMATCH")
        admitted.append(
            GraphRefSource(
                source["tenant_id"],
                source["graph"],
                source["node"],
                source["revision"],
                digest,
            )
        )
    return tuple(admitted)


__all__ = [
    "STUDY_SOURCE_KINDS",
    "FilesystemSource",
    "GraphRefSource",
    "StudySource",
    "admit_study_sources",
]
