"""The per-run lifecycle: consumer steps gated by the received plan.

The run loop (``meristem.graph.executor.iterate_run``) runs one
:class:`RunLifecycle` per run. The plan's received
``HarnessConfig.consumers`` lists the consumer names admitted for the run:
a consumer step added under a name runs only when that name is listed
there, and an unlisted consumer never runs. The host may carry a
pre-configured lifecycle on the plan; without one the loop builds the
default from that list. A consumer error — typically
:class:`ConsumerRefused` — propagates out of the hook and ends the run
failed closed in the run loop's existing refusal flow.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

#: The lifecycle points the run loop calls, in order.
POINTS: tuple[str, ...] = (
    "on_start",
    "before_step",
    "after_step",
    "on_success",
    "on_end",
)

#: The skill span points; they run on demand around one skill invocation.
SKILL_POINTS: tuple[str, ...] = ("on_skill_start", "on_skill_end")

#: A consumer step body: a callable run at one lifecycle point.
ConsumerHook = Callable[..., Awaitable[None]]


class ConsumerRefused(RuntimeError):
    """A consumer step refused; the run fails closed with its code."""

    def __init__(self, code: str) -> None:
        if type(code) is not str or not code.strip():
            raise ValueError("a consumer refusal needs a closed code")
        self.code = code
        super().__init__(f"consumer_refused: {code}")


class RunLifecycle:
    """The consumer steps of one run, gated by the plan's received list.

    ``consumers`` is the received consumer name list (usually
    ``HarnessConfig.consumers``). A step added with :meth:`add` runs only
    when its name is in that list; an unlisted consumer never runs. A step
    error propagates out of the hook that runs it.
    """

    def __init__(self, consumers: Sequence[str] = ()) -> None:
        self._enabled = frozenset(consumers)
        self._steps: dict[str, list[tuple[str, ConsumerHook]]] = {
            point: [] for point in (*POINTS, *SKILL_POINTS)
        }

    @property
    def consumers(self) -> frozenset[str]:
        """The consumer names admitted for this run."""
        return self._enabled

    def enabled(self, name: str) -> bool:
        """Whether a consumer step named ``name`` is admitted for this run."""
        return name in self._enabled

    def add(self, name: str, point: str, hook: ConsumerHook) -> None:
        """Record one consumer step; it runs only when its name is admitted."""
        if point not in POINTS and point not in SKILL_POINTS:
            raise ValueError(f"unknown lifecycle point: {point!r}")
        if type(name) is not str or not name.strip():
            raise ValueError("a consumer step needs a closed name")
        self._steps[point].append((name, hook))

    async def _dispatch(self, point: str, *args: Any) -> None:
        for name, hook in self._steps[point]:
            if name not in self._enabled:
                continue
            result = hook(*args)
            if inspect.isawaitable(result):
                await result

    async def on_start(self, request: Any, plan: Any) -> None:
        """Run the admitted ``on_start`` steps after plan resolution."""
        await self._dispatch("on_start", request, plan)

    async def before_step(self, state: Any) -> None:
        """Run the admitted ``before_step`` steps before one model step."""
        await self._dispatch("before_step", state)

    async def after_step(self, state: Any, step: Any) -> None:
        """Run the admitted ``after_step`` steps after one output."""
        await self._dispatch("after_step", state, step)

    async def on_success(self, state: Any) -> None:
        """Run the admitted ``on_success`` steps at terminal completed."""
        await self._dispatch("on_success", state)

    async def on_end(self, state: Any) -> None:
        """Run the admitted ``on_end`` steps at every terminal status."""
        await self._dispatch("on_end", state)

    async def on_skill_start(self, span: Any) -> None:
        """Run the admitted ``on_skill_start`` steps when a skill starts."""
        await self._dispatch("on_skill_start", span)

    async def on_skill_end(self, span: Any) -> None:
        """Run the admitted ``on_skill_end`` steps when a skill ends."""
        await self._dispatch("on_skill_end", span)


__all__ = [
    "POINTS",
    "SKILL_POINTS",
    "ConsumerHook",
    "ConsumerRefused",
    "RunLifecycle",
]
