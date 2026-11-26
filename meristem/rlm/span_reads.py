"""Served source-text reads through the engine's ReadSpan method."""

from __future__ import annotations

from typing import TYPE_CHECKING

from meristem.harness.run_lifecycle import ConsumerRefused

if TYPE_CHECKING:
    from .runner import _Env


async def read_span(
    env: _Env,
    document_id: str,
    revision: str,
    line_start: int,
    line_end: int,
) -> str:
    """Read one span of a served document through ``ReadSpan``.

    The read travels with the received ``toc_digest``; a read without it
    refuses ``toc_digest_missing``. The engine codes
    ``SPAN_OUT_OF_BOUNDS``, ``TOC_MISSING``, ``SPAN_INVERTED`` and
    ``SPAN_TOO_LARGE`` pass through; an unbound port refuses
    ``engine_decision_unavailable`` and decides nothing. One
    ``span_read`` lineage event records the document, range and digest
    per read; there is no write path.
    """
    toc_digest = env.toc_digest
    if not toc_digest:
        raise ConsumerRefused("toc_digest_missing")
    run_ref = env.lineage.run_id if env.lineage is not None else "rlm"
    answer = await env.ports.engine_read(
        "ReadSpan",
        {
            "document_id": document_id,
            "revision": revision,
            "line_start": line_start,
            "line_end": line_end,
            "toc_digest": toc_digest,
        },
        run_ref=run_ref,
    )
    if answer.refusal_code is not None:
        raise ConsumerRefused(answer.refusal_code)
    if answer.outcome not in ("solved", "committed"):
        raise ConsumerRefused("span_read_unserved")
    lines = answer.payload.get("lines")
    if not isinstance(lines, (list, tuple)) or not all(
        isinstance(line, str) for line in lines
    ):
        raise ConsumerRefused("span_read_unserved")
    # Deferred: runner imports this module at its top level.
    from .runner import _emit_lineage

    _emit_lineage(
        env,
        "span_read",
        document_id=document_id,
        line_start=line_start,
        line_end=line_end,
        toc_digest=toc_digest,
    )
    return "\n".join(lines)


__all__ = ["read_span"]
