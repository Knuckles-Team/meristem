"""Validate host-authorized completed text messages for a fresh harness run."""

from __future__ import annotations

import json
from typing import Any

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
    request: Any, provider: RunCheckpointProvider
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
    if selection.current_run_checkpoint is not None:
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
        return await _read_checkpoint_selection(request, checkpoint_provider)
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
