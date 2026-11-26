"""A scripted engine decision port: queued answers by method, every call recorded."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from meristem import host_ports as hp


def answer(
    payload: Mapping[str, Any] | None = None,
    *,
    outcome: str = "solved",
    refusal: str | None = None,
    options: tuple[str, ...] = (),
    digest: str | None = "rec-1",
) -> hp.EngineAnswer:
    return hp.EngineAnswer(
        outcome=outcome,  # type: ignore[arg-type]
        option_ids=options,
        refusal_code=refusal,
        record_digest=digest,
        payload=dict(payload or {}),
    )


class ScriptedPort:
    """Replays queued answers per method name; unlisted ``log`` calls succeed."""

    def __init__(self, **queues: Any) -> None:
        self.queues = {
            k: list(v) if isinstance(v, list) else [v] for k, v in queues.items()
        }
        self.calls: list[tuple[str, str, Any]] = []
        self.blobs: list[Any] = []

    def _next(self, verb: str, name: str, params: Any) -> hp.EngineAnswer:
        self.calls.append((verb, name, params))
        queue = self.queues.get(name)
        if not queue:
            if verb == "log":
                return answer(outcome="committed")
            raise AssertionError(f"unscripted engine call: {verb} {name}")
        return queue.pop(0) if len(queue) > 1 else queue[0]

    async def decide(self, kind, params, *, run_ref):
        return self._next("decide", kind, params)

    async def read(self, method, params, *, run_ref):
        return self._next("read", method, params)

    async def write(self, method, params, *, run_ref):
        return self._next("write", method, params)

    async def assemble(self, request, *, run_ref):
        return self._next("assemble", "assemble", request)

    async def log(self, op, body, *, run_ref):
        self.blobs.append(dict(body))
        return self._next("log", op, None)

    def names(self, verb: str | None = None) -> list[str]:
        return [n for v, n, _ in self.calls if verb in (None, v)]
