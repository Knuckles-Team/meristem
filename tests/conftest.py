"""Shared fixtures for the harness suite."""

from __future__ import annotations

from collections.abc import AsyncIterator

import ports_support as support
import pytest

from meristem.runtime import DevWorkspace, LocalWorkspace


@pytest.fixture
async def workspace(tmp_path) -> AsyncIterator[DevWorkspace]:
    """A started local developer workspace rooted in ``tmp_path``."""
    async with DevWorkspace(LocalWorkspace(root=tmp_path), run_id="testrun") as ws:
        yield ws


@pytest.fixture(autouse=True)
def _clear_host_ports():
    """Every test starts and ends with no host provider bound."""
    support.reset()
    yield
    support.reset()


@pytest.fixture
def events() -> list[dict]:
    """Run events captured through a fake host run event sink."""
    captured: list[dict] = []
    support.register_run_event_sink(lambda event: captured.append(dict(event)))
    return captured
