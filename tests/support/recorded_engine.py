"""Recorded engine answers for consumer run tests (MERISTEM-HARNESS-CYCLE-R077).

``RecordedEnginePort`` implements ``meristem.host_ports.EngineDecisionPort``
against one fixture: ``tests/fixtures/engine/<case>.json`` carrying
``source_test``, ``engine_commit`` and ``answers``. Each answer entry names
its method and the params it serves. The port replays the next recorded
answer for a matching method and params digest (canonical JSON SHA-256 of
the params), in record order. Any unrecorded call raises
:class:`UnrecordedEngineCall`, failing the test.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from meristem import host_ports as hp

#: Where the recorded fixtures live: ``tests/fixtures/engine/<case>.json``.
FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "engine"


def params_digest(params: Mapping[str, Any]) -> str:
    """Canonical JSON SHA-256 of one params mapping."""
    canonical = json.dumps(
        dict(params), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class UnrecordedEngineCall(LookupError):
    """An engine call the fixture does not record; the test fails."""

    def __init__(self, method: str, digest: str) -> None:
        self.method = method
        self.digest = digest
        super().__init__(f"unrecorded engine call: {method} {digest}")


class RecordedEnginePort:
    """Replays one fixture's recorded engine answers, in record order per key."""

    def __init__(self, case: str | Path | Mapping[str, Any]) -> None:
        if isinstance(case, Mapping):
            data: Mapping[str, Any] = dict(case)
        else:
            path = Path(case)
            if not path.is_absolute():
                if path.suffix != ".json":
                    path = FIXTURES_DIR / f"{path}.json"
                else:
                    path = FIXTURES_DIR / path.name
            data = json.loads(path.read_text(encoding="utf-8"))
        self.source_test: str = str(data.get("source_test", ""))
        self.engine_commit: str = str(data.get("engine_commit", ""))
        self.calls: list[tuple[str, str]] = []
        self._next: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for entry in data.get("answers", ()):
            method = str(entry["method"])
            if "params_digest" in entry:
                digest = str(entry["params_digest"])
            else:
                digest = params_digest(entry.get("params") or {})
            self._next.setdefault((method, digest), []).append(dict(entry["answer"]))

    def _replay(self, method: str, params: Mapping[str, Any]) -> hp.EngineAnswer:
        digest = params_digest(params)
        self.calls.append((method, digest))
        queue = self._next.get((method, digest))
        if not queue:
            raise UnrecordedEngineCall(method, digest)
        record = queue.pop(0)
        return hp.EngineAnswer(
            outcome=record["outcome"],
            option_ids=tuple(record.get("option_ids", ())),
            refusal_code=record.get("refusal_code"),
            record_digest=record.get("record_digest"),
            payload=dict(record.get("payload", ())),
        )

    async def decide(
        self, kind: str, params: Mapping[str, Any], *, run_ref: str
    ) -> hp.EngineAnswer:
        """Replay the recorded answer for one question kind and params."""
        return self._replay(kind, params)

    async def assemble(
        self, request: Mapping[str, Any], *, run_ref: str
    ) -> hp.EngineAnswer:
        """Replay the recorded assembly answer for one request."""
        return self._replay("assemble", request)

    async def log(
        self,
        op: str,
        *,
        run_ref: str,
        params: Mapping[str, Any] | None = None,
    ) -> hp.EngineAnswer:
        """Replay the recorded answer for one decision-log operation."""
        return self._replay("log", {**dict(params or {}), "op": op})

    async def put_blob(self, data: bytes, *, run_ref: str) -> hp.EngineAnswer:
        """Replay the recorded answer for one blob, keyed by its digest."""
        return self._replay("put_blob", {"sha256": hashlib.sha256(data).hexdigest()})

    async def read(
        self, method: str, params: Mapping[str, Any], *, run_ref: str
    ) -> hp.EngineAnswer:
        """Replay the recorded answer for one served read method and params."""
        return self._replay(method, params)

    async def write(
        self, method: str, params: Mapping[str, Any], *, run_ref: str
    ) -> hp.EngineAnswer:
        """Replay the recorded answer for one served write method and params."""
        return self._replay(method, params)


__all__ = [
    "FIXTURES_DIR",
    "RecordedEnginePort",
    "UnrecordedEngineCall",
    "params_digest",
]
