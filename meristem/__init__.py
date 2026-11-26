"""meristem: the in-run agent harness.

The harness holds only the mechanism inside one agent run: applying edits,
checking tool contracts and constraints, pre-emit gates, the developer
workspace action/observation loop, the agent run loop
(``meristem.graph``, ``meristem.rlm``) and the ``host_ports`` protocols through
which the host (arbora) supplies tools, credentials, models and trace sinks.

Facts, storage, retrieval, decisions and learning live in medulla;
serving, admission and the registry live in arbora; connector contracts and
transports live in scion-sdk.
"""

from typing import Any

from meristem._version import __version__

_GRAPH_EXPORTS = frozenset(
    {"create_graph_agent", "execute_graph", "initialize_graph_from_workspace"}
)

#: Modules Arbora may import as part of the supported host/harness boundary.
#: Keep this list explicit: adding a module changes the public host contract.
PUBLIC_HOST_MODULES = (
    "meristem.host_ports",
    "meristem.runtime",
    "meristem.graph",
    "meristem.rlm",
    "meristem.graph.hosted_run",
    "meristem.harness.plan_runtime",
    "meristem.harness.conversation_history",
)


def __getattr__(name: str) -> Any:
    """Load the run-loop entry points on first use (keeps ``import`` light)."""
    if name in _GRAPH_EXPORTS:
        from meristem import graph

        return getattr(graph, name)
    if name == "environment_declarations":
        from meristem import host_ports

        return host_ports.environment_declarations
    raise AttributeError(f"module 'meristem' has no attribute {name!r}")


__all__ = [
    "PUBLIC_HOST_MODULES",
    "__version__",
    "environment_declarations",
    *sorted(_GRAPH_EXPORTS),
]
