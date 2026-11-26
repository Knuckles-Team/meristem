"""Image input through the real run loop."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json

import ports_support as support
import pytest
from pydantic_ai import BinaryContent
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from meristem.graph import RunBudget, RunPlan, RunStatus
from meristem.graph.executor import iterate_run

PNG = BinaryContent(data=b"\x89PNG-bytes", media_type="image/png")
SHA = hashlib.sha256(PNG.data).hexdigest()


def _plan(*, media=("image/png",), fallback="refuse", budget=None, seen=None):
    def respond(messages, info):
        if seen is not None:
            seen.append(messages[-1].parts[-1].content)
        return ModelResponse(parts=[TextPart("ok")])

    return RunPlan(
        model=FunctionModel(respond),
        steps=("describe",),
        input_media_types=media,
        media_fallback=fallback,
        budget=budget or RunBudget(),
    )


async def _events(request, plan):
    return [event async for event in iterate_run(request, plan=plan)]


@pytest.mark.spec("MERISTEM-RUN-R017.2")
async def test_unsupported_image_refuses_or_falls_back_but_never_vanishes():
    seen: list = []
    request = support.request(task="t", attachments=(PNG,))
    # refuse (the default): typed code, no run_started, zero model requests.
    events = await _events(request, _plan(media=(), seen=seen))
    assert [e["type"] for e in events] == ["error"]
    assert "media_unsupported" in events[0]["error"] and seen == []
    # text_placeholder: the image becomes text and one media_fallback event.
    events = await _events(
        request, _plan(media=(), fallback="text_placeholder", seen=seen)
    )
    kinds = [e["type"] for e in events]
    assert "media_fallback" in kinds and "media_attached" not in kinds
    (fallback,) = [e for e in events if e["type"] == "media_fallback"]
    assert fallback["count"] == 1
    placeholder = f"[image omitted: {SHA} image/png {len(PNG.data)}]"
    assert any(placeholder in str(part) for part in seen[0])
    assert events[-1]["result"]["media_fallbacks"] == 1


@pytest.mark.spec("MERISTEM-RUN-R017.3")
async def test_events_and_checkpoint_hold_digests_and_never_bytes():
    saved: list[str] = []

    class Port:
        admission_identity = "admitted/run"

        async def restore(self):
            return None

        async def save(self, content):
            saved.append(content)

    @contextlib.asynccontextmanager
    async def scope(request, plan):
        yield Port()

    support.register_run_checkpoint_factory(scope)
    request = support.request(task="t", attachments=(PNG,))
    events = await _events(request, _plan())
    (attached,) = [e for e in events if e["type"] == "media_attached"]
    assert (attached["sha256"], attached["media_type"], attached["bytes"]) == (
        SHA,
        "image/png",
        len(PNG.data),
    )
    assert attached["position"] == 0
    blob = json.dumps(events, default=str) + "".join(saved)
    encoded = base64.b64encode(PNG.data).decode()
    assert "PNG-bytes" not in blob and encoded not in blob
    # The checkpoint binds the run to the image through its digest descriptor.
    assert all(json.loads(content)["plan_digest"] for content in saved)


@pytest.mark.spec("MERISTEM-RUN-R017.3")
async def test_resolution_by_digest_needs_a_known_digest():
    support.reset()
    refs = [{"sha256": SHA, "media_type": "image/png"}]
    request = support.request(task="t", metadata={"media_refs": refs})
    # No resolver bound: refused.
    events = await _events(request, _plan())
    assert "media_unavailable" in events[-1]["error"]

    # An unknown digest is refused; a known one reaches the model.
    async def resolver(digest):
        return PNG.data if digest == SHA else None

    support.PORTS.media_resolver = resolver
    seen: list = []
    events = await _events(request, _plan(seen=seen))
    assert events[-1]["result"]["status"] == "completed"
    bad = support.request(
        task="t",
        metadata={"media_refs": [{"sha256": "0" * 64, "media_type": "image/png"}]},
    )
    assert "media_unavailable" in (await _events(bad, _plan()))[-1]["error"]


@pytest.mark.spec("MERISTEM-RUN-R017.4")
async def test_image_reservation_counts_against_token_limit_and_measured_usage_replaces_it():
    seen: list = []
    budget = RunBudget(total_tokens_limit=900, image_token_cost=1000)
    events = await _events(
        support.request(task="t", attachments=(PNG,)), _plan(budget=budget, seen=seen)
    )
    assert [e["type"] for e in events] == ["error"]
    assert "budget_exceeded" in events[0]["error"] and seen == []
    # Within the limit the estimate is not charged: usage is what the model reports.
    ok = RunBudget(total_tokens_limit=5000, image_token_cost=1000)
    events = await _events(
        support.request(task="t", attachments=(PNG,)), _plan(budget=ok, seen=seen)
    )
    result = events[-1]["result"]
    assert result["status"] == "completed"
    assert result["usage"]["requests"] == 1 and result["usage"]["input_tokens"] < 1000


@pytest.mark.spec("MERISTEM-RUN-R017.3")
async def test_restart_resolves_checkpointed_images_by_digest_or_refuses():
    support.reset()
    saved: list[str] = []

    class Port:
        admission_identity = "admitted/run"

        async def restore(self):
            return saved[-1] if saved else None

        async def save(self, content):
            saved.append(content)

    @contextlib.asynccontextmanager
    async def scope(request, plan):
        yield Port()

    support.register_run_checkpoint_factory(scope)

    def two_steps():
        plan = _plan()
        return RunPlan(
            model=plan.model, steps=("one", "two"), input_media_types=("image/png",)
        )

    request = support.request(task="t", run_id="r", attachments=(PNG,))
    events = iterate_run(request, plan=two_steps())
    async for event in events:
        if event["type"] == "node_transition":
            break
    await events.aclose()
    assert "iVBORy1ieXRlcw==" not in saved[-1] and SHA in saved[-1]

    snapshot = saved[-1]

    async def resolver(digest):
        return PNG.data if digest == SHA else None

    support.PORTS.media_resolver = resolver
    resumed = await _events(request, two_steps())
    assert resumed[-1]["result"]["status"] == "completed"
    # Without a resolver the restore refuses before any model call.
    support.PORTS.media_resolver = None
    saved.append(snapshot)
    refused = await _events(request, two_steps())
    assert "media_unavailable" in refused[-1]["error"]


def _two_step_plan(limit, requests):
    def respond(messages, info):
        requests.append(list(messages))
        return ModelResponse(parts=[TextPart("ok")])

    return RunPlan(
        model=FunctionModel(respond),
        steps=("one", "two"),
        input_media_types=("image/png",),
        budget=RunBudget(history_token_limit=limit, image_token_cost=1000),
    )


def _has_image(messages):
    return any(
        isinstance(item, BinaryContent)
        for message in messages
        for part in message.parts
        for item in (
            part.content if isinstance(getattr(part, "content", None), list) else []
        )
    )


@pytest.mark.spec("MERISTEM-RUN-R017.1")
async def test_history_keeps_image_parts_and_trimming_emits_media_dropped():
    request = support.request(task="t", attachments=(PNG,))
    # Within the received limit the second step still sees the image part.
    kept: list = []
    events = await _events(request, _two_step_plan(5000, kept))
    assert _has_image(kept[1])
    assert "media_dropped" not in [e["type"] for e in events]
    # Over the limit the oldest turn goes, and its image goes with a digest event.
    trimmed: list = []
    events = await _events(request, _two_step_plan(500, trimmed))
    assert not _has_image(trimmed[1])
    (dropped,) = [e for e in events if e["type"] == "media_dropped"]
    assert dropped["sha256"] == SHA and dropped["bytes"] == len(PNG.data)
    assert "89PNG" not in json.dumps(dropped)
    assert events[-1]["result"]["status"] == "completed"


@pytest.mark.spec("MERISTEM-RUN-R017.5")
async def test_iterate_run_png_reaches_model_request_end_to_end():
    seen: list = []
    budget = RunBudget(total_tokens_limit=5000, image_token_cost=1000)
    request = support.request(task="t", attachments=(PNG,))
    events = await _events(request, _plan(budget=budget, seen=seen))
    kinds = [e["type"] for e in events]
    # The model request carries the binary part; the digest event follows run_started.
    assert any(isinstance(part, BinaryContent) for part in seen[0])
    assert kinds.index("media_attached") == kinds.index("run_started") + 1
    (attached,) = [e for e in events if e["type"] == "media_attached"]
    assert attached["sha256"] == SHA and attached["position"] == 0
    assert events[-1]["result"]["status"] == "completed"
    # The reservation refuses before any request; the fallback path runs.
    seen.clear()
    tight = RunBudget(total_tokens_limit=900, image_token_cost=1000)
    refused = await _events(request, _plan(budget=tight, seen=seen))
    assert "budget_exceeded" in refused[-1]["error"] and seen == []
    fallback = await _events(
        request, _plan(media=(), fallback="text_placeholder", seen=seen)
    )
    assert "media_fallback" in [e["type"] for e in fallback]


def test_status_enum_is_unchanged():
    assert RunStatus.COMPLETED.value == "completed"
