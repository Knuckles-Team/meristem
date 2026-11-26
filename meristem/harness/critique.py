"""The one critique read of the run loop: pages of ``TraceCritique``.

The page size is the received engine value; the harness never sets it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineDecisionPort, EngineDecisionRefused

READ_METHOD = "TraceCritique"
MAX_PAGES = 10_000


async def read_critique(
    query: Mapping[str, Any], port: EngineDecisionPort | None, *, run_ref: str
) -> list[Mapping[str, Any]]:
    """Read every critique page in order until the cursor is empty.

    ``query`` is the received query (snapshot, root, tenant, the engine's page
    ``limit``); the harness adds only the cursor. Engine codes pass through. A
    cursor seen twice refuses ``critique_page_out_of_order``. The cursors are
    kept on each returned page under ``cursor`` so they enter the trace.
    """
    if port is None:
        raise EngineDecisionRefused("engine_decision_unavailable")
    pages: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    cursor: str | None = None
    for _ in range(MAX_PAGES):
        body = {**query, "cursor": cursor}
        answer = await port.read(READ_METHOD, {"query": body}, run_ref=run_ref)
        if answer.refusal_code is not None:
            raise ConsumerRefused(answer.refusal_code)
        pages.append({**answer.payload, "cursor": cursor})
        nxt = answer.payload.get("next_cursor")
        if not nxt:
            return pages
        if not isinstance(nxt, str) or nxt in seen or nxt == cursor:
            raise ConsumerRefused("critique_page_out_of_order")
        seen.add(nxt)
        cursor = nxt
    raise ConsumerRefused("critique_page_out_of_order")
