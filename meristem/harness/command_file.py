"""The immutable command file the run verifies against its record.

The command file seals the retrieval configuration and the inference
mode of a run. The harness reads it once, computes both digests and
asks the engine for the retrieval plan; the file is used only when the
sealed record carries the same two digests.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineDecisionPort, EngineDecisionRefused


@dataclass(frozen=True, slots=True)
class CommandFile:
    """The sealed digests of one verified command file.

    Both digests are SHA-256 of the canonical JSON of the file's
    sections, 64 lowercase hex characters.
    """

    config_digest: str
    mode_digest: str
    record_digest: str | None = None


def _canonical_digest(value: Any) -> str:
    """SHA-256 of one received section as canonical JSON."""
    canonical = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def load_command_file(
    path: str | Path,
    port: EngineDecisionPort | None,
    *,
    run_ref: str,
) -> CommandFile:
    """Verify the immutable command file against its sealed record.

    The file is read once and its schema validated: a JSON object with
    the ``command_config`` and ``inference_mode`` sections. Both
    digests are computed as SHA-256 and sent as the params
    ``retrieval.command_config_digest`` and
    ``retrieval.inference_mode_digest`` of the ``retrieval_plan``
    question. The file is used only when the sealed record carries the
    same two digests; a differing record refuses
    ``command_mode_digest_mismatch``, an unreadable or foreign file
    refuses ``command_file_schema_invalid``. Engine codes pass
    through; with no bound port it refuses
    ``engine_decision_unavailable`` and the guarded action does not
    start.
    """
    if port is None:
        raise EngineDecisionRefused("engine_decision_unavailable")
    try:
        document = json.loads(Path(path).read_bytes())
    except (OSError, json.JSONDecodeError):
        raise ConsumerRefused("command_file_schema_invalid") from None
    if not isinstance(document, Mapping):
        raise ConsumerRefused("command_file_schema_invalid")
    config = document.get("command_config")
    mode = document.get("inference_mode")
    if not isinstance(config, Mapping) or not isinstance(mode, Mapping):
        raise ConsumerRefused("command_file_schema_invalid")
    config_digest = _canonical_digest(config)
    mode_digest = _canonical_digest(mode)
    answer = await port.decide(
        "retrieval_plan",
        {
            "retrieval.command_config_digest": config_digest,
            "retrieval.inference_mode_digest": mode_digest,
        },
        run_ref=run_ref,
    )
    if answer.refusal_code is not None:
        raise ConsumerRefused(answer.refusal_code)
    record = answer.payload if answer.outcome in ("solved", "committed") else {}
    if (
        record.get("retrieval.command_config_digest") != config_digest
        or record.get("retrieval.inference_mode_digest") != mode_digest
    ):
        raise ConsumerRefused("command_mode_digest_mismatch")
    return CommandFile(
        config_digest=config_digest,
        mode_digest=mode_digest,
        record_digest=answer.record_digest,
    )


@dataclass(frozen=True, slots=True)
class CommandConfig:
    """The frozen command config of one mode; the digest echoes on every invocation."""

    mode_id: str
    config_digest: str
    values: Mapping[str, Any]


async def apply_command_config(
    port: EngineDecisionPort | None,
    *,
    mode_id: str,
    expected_mode_revision: int,
    expected_config_digest: str,
    run_ref: str,
) -> CommandConfig:
    """Apply the engine's command config immutably (MERISTEM-HARNESS-CYCLE-R018.1).

    Reads the options ``mode:<id>`` and ``config:<digest>``, validates the config
    schema and freezes it; a config that is not a JSON object, or whose digest
    differs from the answer's, refuses ``command_config_schema_invalid``.
    """
    from types import MappingProxyType

    from meristem.harness import engine_step

    answer = await engine_step.decide(
        port,
        "command_config",
        {
            "mode_id": mode_id,
            "expected_mode_revision": expected_mode_revision,
            "expected_config_digest": expected_config_digest,
        },
        run_ref=run_ref,
    )
    options = set(answer.option_ids)
    config = answer.payload.get("config")
    digest = next((o[7:] for o in options if o.startswith("config:")), "")
    if (
        f"mode:{mode_id}" not in options
        or not isinstance(config, Mapping)
        or not digest
        or _canonical_digest(config) != digest
    ):
        raise ConsumerRefused("command_config_schema_invalid")
    return CommandConfig(mode_id, digest, MappingProxyType(dict(config)))


__all__ = ["CommandConfig", "CommandFile", "apply_command_config", "load_command_file"]
