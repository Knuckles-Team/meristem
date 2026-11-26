"""Validate host-authorized completed text messages for a fresh harness run."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic_ai import BinaryContent
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from meristem.host_ports import (
    CompletedChildBinding,
    HostPorts,
    RunCheckpointProvider,
    RunCheckpointSelection,
)


def child_history_envelope(content: str | None, binding: Any, *, run_id: str) -> str:
    """Validate the host's explicit child selection before ordinary hydration.

    The host owns authorization; this checks that the selected payload matches
    its independently supplied binding and contains only completed text turns.
    """
    try:
        if not isinstance(binding, CompletedChildBinding):
            raise ValueError
        fields = (
            "child_id",
            "conversation_id",
            "predecessor_run_id",
            "history_revision",
        )
        if any(
            type(getattr(binding, key)) is not str
            or not getattr(binding, key).strip()
            or len(getattr(binding, key).encode()) > 512
            for key in fields
        ):
            raise ValueError
        if not isinstance(content, str) or len(content.encode()) > 1_048_576:
            raise ValueError
        value = json.loads(content)
        if not isinstance(value, dict) or set(value) != {
            "version",
            "phase",
            "messages",
            *fields,
        }:
            raise ValueError
        if (
            type(value["version"]) is not int
            or value["version"] != 1
            or value["phase"] != "completed"
        ):
            raise ValueError
        if any(value[key] != getattr(binding, key) for key in fields):
            raise ValueError
        normalized = json.dumps(
            {
                "version": 1,
                "phase": "completed",
                "run_id": binding.predecessor_run_id,
                "messages": value["messages"],
            }
        )
        decode_completed_history(normalized, run_id=run_id)
        return normalized
    except Exception:
        raise ValueError("completed child history is invalid") from None


def decode_completed_history(content: str, *, run_id: str) -> list[ModelMessage]:
    """Decode only completed text turns, never budgets or execution authority."""
    try:
        if not isinstance(content, str) or len(content.encode()) > 1_048_576:
            raise ValueError
        value = json.loads(content)
        if (
            not isinstance(value, dict)
            or set(value) != {"version", "phase", "run_id", "messages"}
            or type(value["version"]) is not int
            or value["version"] != 1
            or value["phase"] != "completed"
            or not isinstance(value["run_id"], str)
            or not value["run_id"].strip()
            or value["run_id"] == run_id
            or not isinstance(value["messages"], list)
            or not 2 <= len(value["messages"]) <= 1024
            or len(value["messages"]) % 2
        ):
            raise ValueError
        messages: list[ModelMessage] = []
        for index, message in enumerate(value["messages"]):
            if (
                not isinstance(message, dict)
                or set(message) != {"role", "text"}
                or message["role"] != ("assistant" if index % 2 else "user")
                or not isinstance(message["text"], str)
            ):
                raise ValueError
            if index % 2:
                messages.append(ModelResponse(parts=[TextPart(message["text"])]))
            else:
                messages.append(ModelRequest(parts=[UserPromptPart(message["text"])]))
        return messages
    except Exception:
        raise ValueError("completed conversation history is invalid") from None


async def _read_checkpoint_selection(
    request: Any, provider: RunCheckpointProvider, *, restorable: bool = False
) -> str | None:
    try:
        selection = await provider(request)
    except Exception:
        raise RuntimeError("run checkpoint provider failed") from None
    if (
        not isinstance(selection, RunCheckpointSelection)
        or type(selection.version) is not int
        or selection.version != 1
        or not isinstance(selection.run_id, str)
        or not selection.run_id.strip()
        or selection.run_id != getattr(request, "run_id", None)
    ):
        raise ValueError("run checkpoint selection is invalid")
    # Presence is authoritative even for empty, malformed or in-flight content.
    # Never reinterpret it as prior history or silently start a replacement run.
    # A current-run checkpoint is restored by the bound ``checkpoint_store``
    # transaction (``RunCheckpoint.restore``), never from this read. Without a
    # bound store its presence refuses before any effect.
    if selection.current_run_checkpoint is not None and not restorable:
        raise RuntimeError("current-run checkpoint restore is unavailable")
    if selection.completed_history is not None and not isinstance(
        selection.completed_history, str
    ):
        raise ValueError("run checkpoint selection is invalid")
    if selection.completed_child is not None:
        return child_history_envelope(
            selection.completed_history,
            selection.completed_child,
            run_id=selection.run_id,
        )
    return selection.completed_history


async def read_run_history(ports: HostPorts, request: Any) -> str | None:
    """Use one checkpoint read; legacy history is only for an unbound new port."""
    checkpoint_provider = ports.run_checkpoints
    if checkpoint_provider is not None:
        return await _read_checkpoint_selection(
            request, checkpoint_provider, restorable=ports.run_checkpoint_bound()
        )
    provider = ports.run_history
    if provider is None:
        return None
    try:
        return await provider(request)
    except Exception:
        raise RuntimeError("conversation history provider failed") from None


async def read_recursive_run_history(
    ports: HostPorts, request: Any | None
) -> str | None:
    """Read one bound selection only for an explicitly identified recursive run."""
    checkpoint_provider = ports.run_checkpoints
    history_provider = ports.run_history
    if checkpoint_provider is None and history_provider is None:
        return None
    if request is None:
        raise ValueError("recursive history selection needs admitted lineage")
    if checkpoint_provider is not None:
        return await _read_checkpoint_selection(request, checkpoint_provider)
    assert history_provider is not None
    try:
        return await history_provider(request)
    except Exception:
        raise RuntimeError("conversation history provider failed") from None


def _parts(message: ModelMessage) -> list[Any]:
    out: list[Any] = []
    for part in message.parts:
        content = getattr(part, "content", None)
        out.extend(content if isinstance(content, (list, tuple)) else [content])
    return out


def _weight(message: ModelMessage, image_cost: int) -> int:
    """Estimated tokens of one message: a quarter of the text, a received cost per image."""
    total = 0
    for item in _parts(message):
        if isinstance(item, BinaryContent):
            total += image_cost
        elif isinstance(item, str):
            total += len(item) // 4 + 1
    return total


def _turns(history: list[ModelMessage]) -> list[list[ModelMessage]]:
    """Split at each request that opens with a user prompt, keeping tool pairs whole."""
    turns: list[list[ModelMessage]] = []
    for message in history:
        opens = isinstance(message, ModelRequest) and any(
            isinstance(part, UserPromptPart) for part in message.parts
        )
        if opens or not turns:
            turns.append([])
        turns[-1].append(message)
    return turns


def trim_history(
    history: list[ModelMessage], limit: int | None, image_cost: int
) -> tuple[list[ModelMessage], list[dict[str, Any]]]:
    """Drop the oldest whole turns until the history fits the received limit.

    The pending prompt is not in the history, so the limit may drop every
    stored turn. Returns the kept messages and one digest
    descriptor per dropped image, so the caller can emit ``media_dropped``;
    an image never leaves the history without that record.
    """
    if limit is None:
        return history, []
    turns = _turns(history)
    weights = [sum(_weight(m, image_cost) for m in turn) for turn in turns]
    dropped: list[dict[str, Any]] = []
    first = 0
    while first < len(turns) and sum(weights[first:]) > limit:
        for message in turns[first]:
            for item in _parts(message):
                if isinstance(item, BinaryContent):
                    dropped.append(
                        {
                            "sha256": hashlib.sha256(item.data).hexdigest(),
                            "media_type": item.media_type,
                            "bytes": len(item.data),
                        }
                    )
        first += 1
    return [m for turn in turns[first:] for m in turn], dropped
