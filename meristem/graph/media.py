"""Image input in the run loop: admission policy, digests and the byte budget.

The harness applies the values the plan receives: the admitted media types,
the fallback mode and the per-image token cost. It never drops an image
without a typed refusal or a ``media_fallback`` event, and it never records
raw bytes; events and checkpoints hold digests.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai import BinaryContent

from meristem.host_ports import HostPorts

from .state import RunPlan

IMAGE_PREFIX = "image/"


class MediaRefused(ValueError):
    """A closed media refusal; ``code`` is the typed code the run fails with."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def descriptor(raw: bytes, media_type: str) -> dict[str, Any]:
    """The digest descriptor of one part; the one source for events and checkpoints."""
    return {
        "media_type": media_type,
        "bytes": len(raw),
        "digest": hashlib.sha256(raw).hexdigest(),
    }


def placeholder(part: BinaryContent) -> str:
    """The text that stands in for one unsupported image."""
    info = descriptor(part.data, part.media_type)
    return f"[image omitted: {info['digest']} {info['media_type']} {info['bytes']}]"


@dataclass(slots=True)
class MediaAdmission:
    """What the policy decided for one request's attachments."""

    parts: list[str | BinaryContent] = field(default_factory=list)
    attached: list[dict[str, Any]] = field(default_factory=list)
    fallbacks: int = 0

    @property
    def image_count(self) -> int:
        return len(self.attached)


def apply_policy(parts: Sequence[str | BinaryContent], plan: RunPlan) -> MediaAdmission:
    """Admit each image, refuse the run, or replace it with a placeholder.

    A listed image type is admitted. An unlisted one refuses
    ``media_unsupported`` under mode ``refuse`` and becomes a placeholder
    under ``text_placeholder``.
    """
    mode = plan.media_fallback
    if mode not in ("refuse", "text_placeholder"):
        raise ValueError("media_fallback must be refuse or text_placeholder")
    admission = MediaAdmission()
    for position, part in enumerate(parts):
        listed = (
            isinstance(part, BinaryContent)
            and part.media_type in plan.input_media_types
        )
        if isinstance(part, BinaryContent) and part.media_type.startswith(IMAGE_PREFIX):
            if not listed:
                if mode == "refuse":
                    raise MediaRefused("media_unsupported")
                admission.parts.append(placeholder(part))
                admission.fallbacks += 1
                continue
            info = descriptor(part.data, part.media_type)
            admission.attached.append(
                {
                    "sha256": info["digest"],
                    "media_type": info["media_type"],
                    "bytes": info["bytes"],
                    "position": position,
                }
            )
        admission.parts.append(part)
    return admission


def reserve_images(admission: MediaAdmission, plan: RunPlan) -> int:
    """Reserve the per-image token cost against the token limit.

    The reservation is a pre-flight check only: it is never added to the run's
    usage, so the measured provider usage of each response is what the run
    counts. A reservation above the limit refuses ``budget_exceeded`` before
    any model request.
    """
    cost = plan.budget.image_token_cost
    if type(cost) is not int or cost < 0:
        raise ValueError("invalid image token cost")
    reserved = admission.image_count * cost
    limit = plan.budget.total_tokens_limit
    if limit is not None and reserved > limit:
        raise MediaRefused("budget_exceeded")
    return reserved


async def resolve_refs(
    refs: Sequence[Mapping[str, Any]], ports: HostPorts
) -> list[BinaryContent]:
    """Resolve bytes by digest through the host port, checking each digest.

    An absent port, an unknown digest or bytes that do not match refuse
    ``media_unavailable``.
    """
    resolver = ports.media_resolver
    if resolver is None:
        raise MediaRefused("media_unavailable")
    resolved: list[BinaryContent] = []
    for ref in refs:
        digest = str(ref.get("sha256", ""))
        raw = await resolver(digest)
        if not isinstance(raw, bytes) or hashlib.sha256(raw).hexdigest() != digest:
            raise MediaRefused("media_unavailable")
        resolved.append(BinaryContent(data=raw, media_type=str(ref.get("media_type"))))
    return resolved
