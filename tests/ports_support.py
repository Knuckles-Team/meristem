"""Test support: one HostPorts per test, bound by the registrar helpers."""

from __future__ import annotations

from typing import Any

from meristem.graph.state import RunRequest
from meristem.host_ports import HostPorts

PORTS = HostPorts()


def reset() -> None:
    """Unbind every provider so the next test starts with no host."""
    for name in vars(HostPorts()):
        setattr(PORTS, name, None)


def request(*args: Any, **kwargs: Any) -> RunRequest:
    """A run request that carries this test's ports."""
    kwargs.setdefault("ports", PORTS)
    return RunRequest(*args, **kwargs)


def register_run_decision_provider(value: Any) -> None:
    PORTS.decide = value


def register_tool_caller(value: Any) -> None:
    PORTS.caller = value


def register_model_provider(value: Any) -> None:
    PORTS.models = value


def register_run_event_sink(value: Any) -> None:
    PORTS.events = value


def register_durable_run_event_factory(value: Any) -> None:
    PORTS.durable_run_events = value


def register_harness_probe_provider(value: Any) -> None:
    PORTS.probe = value


def register_harness_cycle_provider(value: Any) -> None:
    PORTS.cycle = value


def register_active_child_input_factory(value: Any) -> None:
    PORTS.active_child_input = value


def register_native_toolset_provider(value: Any) -> None:
    PORTS.native = value


def register_fleet_catalog_provider(value: Any) -> None:
    PORTS.fleet = value


def register_authority_keepalive(value: Any) -> None:
    PORTS.keepalive = value


def register_remote_toolset_provider(value: Any) -> None:
    PORTS.remote = value


def register_tool_client_factory(value: Any) -> None:
    PORTS.tool_client = value


def register_outbound_credential_provider(value: Any) -> None:
    PORTS.credentials = value


def register_delegated_identity_provider(value: Any) -> None:
    PORTS.identity = value


def register_scaffold_publication_factory(value: Any) -> None:
    PORTS.scaffold_publication = value


def register_optional_scaffold_publication_factory(value: Any) -> None:
    PORTS.scaffold_publication = value


def register_run_history_provider(value: Any) -> None:
    PORTS.run_history = value


def register_run_checkpoint_provider(value: Any) -> None:
    PORTS.run_checkpoints = value


def register_run_checkpoint_factory(value: Any) -> None:
    PORTS.checkpoint_factory = value
