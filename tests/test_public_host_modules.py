"""Public host-module contract tests."""

from __future__ import annotations

import importlib

import pytest

import meristem


@pytest.mark.spec("MERISTEM-RUN-R008")
def test_public_host_modules_import_and_are_documented() -> None:
    """Every declared host import remains an importable documented module."""
    assert meristem.PUBLIC_HOST_MODULES == (
        "meristem.host_ports",
        "meristem.runtime",
        "meristem.graph",
        "meristem.rlm",
        "meristem.graph.hosted_run",
        "meristem.harness.plan_runtime",
        "meristem.harness.conversation_history",
    )
    for module_name in meristem.PUBLIC_HOST_MODULES:
        module = importlib.import_module(module_name)
        assert module.__doc__ and module.__doc__.strip()
