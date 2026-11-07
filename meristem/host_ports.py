"""Host-provided ports the harness receives from arbora.

CONCEPT:MERISTEM-ORCH.adapter.transport-toolset-factory

meristem is the agent harness only. It never serves, registers or
connects to a tool surface itself and carries no MCP code: arbora owns the
MCP serving surface, every MCP client connection and every outbound
credential. When the harness runs inside arbora, arbora builds one :class:`HostPorts`
holding these plain typed providers and hands it to each run through
``RunRequest.ports``; the harness keeps no provider in module state:

* a **native toolset provider** that binds a caller-granted subset of arbora's
  own served operations for one in-process delegation (no self-HTTP);
* a **fleet catalog provider** that maps each configured fleet server to the
  public tool prefix arbora serves it under and its client endpoint;
* a **remote toolset provider** that builds the pydantic-ai toolset for one
  fleet endpoint or one client configuration file (arbora's MCP client);
* a **tool client factory** that opens a ``list_tools``/``call_tool`` client
  session for one endpoint (arbora's MCP client);
* an **outbound credential provider** (service-account auth, header, status,
  forced refresh) and a **delegated identity provider** (the caller's user
  token and on-behalf-of exchange);
* an **authority keepalive** that renews the caller's renewable session lease
  for the whole of one delegation;
* a **run decision provider** that answers, for one run request, the run plan:
  model, tool grant, plan steps, budgets, stop rules and harness settings (the
  decision itself comes from epistemic-graph through arbora);
* a **model provider** that turns a model id from that plan into a pydantic-ai
  model with arbora's credentials attached;
* a **run event sink** that receives every event of one run (arbora writes
  the trace into epistemic-graph);
* a **harness cycle provider** that drives one harness-evolution tick per
  task (propose, probe, compose, persist, reflect) through the served
  decision and graph ports.

Without a bound provider every binding port fails closed (raises) and
every read port reports the empty answer, never inventing a surface of its own.
"""

from __future__ import annotations

import contextlib
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Iterable,
    Mapping,
    Sequence,
)
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

ToolOutcome = Literal["ok", "refused", "failed"]


@dataclass(frozen=True, slots=True)
class ToolStepResult:
    """Structured result returned by a host-bound :class:`ToolCaller`."""

    result: Any = None
    outcome: ToolOutcome = "ok"
    error: str | None = None

    def __post_init__(self) -> None:
        # Accept the natural ``ToolStepResult("ok", value)`` positional form
        # as well as the named ``result=..., outcome=...`` form. The latter is
        # the stable wire shape; this small normalization keeps host adapters
        # from having to coordinate constructor ordering.
        if self.outcome not in ("ok", "refused", "failed"):
            if isinstance(self.result, str) and self.result in (
                "ok",
                "refused",
                "failed",
            ):
                positional_outcome = self.result
                positional_result = self.outcome
                object.__setattr__(self, "result", positional_result)
                object.__setattr__(self, "outcome", positional_outcome)
            else:
                raise ValueError("tool result outcome must be ok, refused or failed")
        if self.error is not None and not isinstance(self.error, str):
            raise ValueError("tool result error must be a string or None")

    @property
    def status(self) -> ToolOutcome:
        """Compatibility spelling for callers that use status terminology."""

        return self.outcome

    @property
    def output(self) -> Any:
        """Compatibility spelling for the structured result value."""

        return self.result


class PlanItem(BaseModel):
    """One immutable scaffold item in model-supplied order."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", strict=True, revalidate_instances="always"
    )

    text: str = Field(min_length=1)
    status: Literal["pending", "in_progress", "done"]


class PlanState:
    """One run's ordered scaffold; validate a complete replacement before applying it."""

    def __init__(self) -> None:
        self._items: tuple[PlanItem, ...] = ()
        self._revision = 0
        self._lock = Lock()

    @property
    def items(self) -> tuple[PlanItem, ...]:
        """An immutable view that remains stable after later replacements."""
        return self.read_versioned()[1]

    def read_versioned(self) -> tuple[int, tuple[PlanItem, ...]]:
        """Read a consistent revision/item pair without changing either value."""
        with self._lock:
            return self._revision, self._items

    def replace(self, items: Sequence[PlanItem]) -> tuple[PlanItem, ...]:
        """Replace all items atomically; invalid input leaves the old list intact."""
        return self.replace_versioned(items)[1]

    def replace_versioned(
        self, items: Sequence[PlanItem]
    ) -> tuple[int, tuple[PlanItem, ...]]:
        """Return exactly the accepted revision and items under the update lock."""
        replacement = tuple(PlanItem.model_validate(item) for item in items)
        with self._lock:
            self._items = replacement
            self._revision += 1
            return self._revision, replacement


class HostToolError(RuntimeError):
    """A tool call the host executed reported a tool-level error."""


class ClientCapabilityTransport(Protocol):
    """One host-bound client session; the host owns grants and approval state."""

    async def call(self, method: str, params: Mapping[str, Any]) -> str:
        """Dispatch once or raise ClientCapabilityRefused; never infer approval."""
        ...


class ClientCapabilityRefused(RuntimeError):
    """Closed host refusal safe to return to a model without private details."""

    def __init__(self, reason: str) -> None:
        allowed = {
            "permission_required",
            "permission_denied",
            "client_disconnected",
            "capability_not_declared",
            "authority_unavailable",
        }
        self.reason = reason if reason in allowed else "authority_unavailable"
        super().__init__(self.reason)


class ToolClient(Protocol):
    """One open client session onto a tool endpoint, provided by the host."""

    async def list_tools(self) -> list[Any]: ...

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None, **kwargs: Any
    ) -> Any: ...

    async def read_resource(self, uri: str) -> list[Any]: ...


@dataclass(frozen=True)
class NativeToolsetProvider:
    """arbora's in-process binder for its own served operations."""

    available_tools: Callable[[], Iterable[str]]
    build_toolset: Callable[[list[str], str], Any]


@dataclass(frozen=True)
class FleetServer:
    """One fleet server as arbora serves it: public prefix + client endpoint."""

    prefix: str
    url: str = ""


@dataclass(frozen=True)
class RemoteToolsetProvider:
    """arbora's builder for client toolsets the harness hands to an agent.

    ``build_url_toolset(url, toolset_id, timeout)`` returns one toolset bound to
    ``url`` with arbora's outbound credentials attached;
    ``load_config_toolsets(path)`` returns the toolsets a client configuration
    file declares.
    """

    build_url_toolset: Callable[[str, str | None, float], Any]
    load_config_toolsets: Callable[[Path], list[Any]]


@dataclass(frozen=True)
class OutboundCredentialProvider:
    """arbora's service-account credentials for outbound calls."""

    auth: Callable[[], Any]
    header: Callable[[], dict[str, str]]
    status: Callable[[], dict[str, object]]
    refresh: Callable[[], None]


@dataclass(frozen=True)
class DelegatedIdentityProvider:
    """arbora's caller identity: the user token and on-behalf-of exchange."""

    user_token: Callable[[], str | None]
    exchange_for_agent: Callable[[str], str | None]


ToolClientFactory = Callable[..., contextlib.AbstractAsyncContextManager[ToolClient]]

RunDecisionProvider = Callable[[Any], Any]
"""``(RunRequest) -> RunPlan`` (or an awaitable of it); see ``meristem.graph``."""

ToolCaller = Callable[[str, str, Mapping[str, Any], str], Awaitable[ToolStepResult]]
"""``(server, tool, arguments, task_id) -> ToolStepResult`` for one run."""

ModelProvider = Callable[[str], Any]
"""``(model_id) -> pydantic-ai Model`` with the host's credentials attached."""

RunEventSink = Callable[[Mapping[str, Any]], None]
"""Receives one run event (a JSON-shaped mapping) in emission order."""


class DurableRunEventPublisher(Protocol):
    """One host-owned durable writer bound to the admitted run."""

    async def publish(self, kind: str, content: Mapping[str, Any]) -> None:
        """Persist one validated public model/tool observation."""
        ...


DurableRunEventFactory = Callable[
    [Any], contextlib.AbstractAsyncContextManager[DurableRunEventPublisher | None]
]
"""Build one optional durable event publisher for a run request."""


@dataclass(frozen=True, slots=True)
class ActiveChildBinding:
    """Identity supplied by the host after child admission."""

    child_run_id: str
    parent_run_id: str
    admission_id: str
    lifecycle_revision: int


@dataclass(frozen=True, slots=True)
class ActiveChildMessage:
    """A bounded host-claimed message, not an AU admission decision."""

    binding: ActiveChildBinding
    message_id: str
    event_id: str
    content: str


class ActiveChildInputPort(Protocol):
    """Host-owned ordered admission, duplicate control and durable receipts.

    Receipt acknowledges insertion into this live run's volatile history, not
    model consumption. The host must retain uncertainty on a crash or failed
    receipt, and its context manager must settle undelivered inputs on exit.
    """

    binding: ActiveChildBinding

    async def take_ready(self) -> tuple[ActiveChildMessage, ...]:
        """Claim an ordered batch after revalidating admission and lifecycle.

        The host never returns a claimed message twice for this live run.
        """
        ...

    async def report_applied(self, message: ActiveChildMessage) -> None:
        """Durably acknowledge one message; failures leave host outcome uncertain."""
        ...


ActiveChildInputFactory = Callable[
    [Any], contextlib.AbstractAsyncContextManager[ActiveChildInputPort | None]
]


HarnessProbeProvider = Callable[..., Awaitable[Mapping[str, Any]]]
"""Host admission and execution of one materialized probe; not measurement proof."""

HarnessCycleProvider = Callable[..., Any]
"""``(request, plan, *, task_class, graph, config, root, mode) ->
CycleOutcome`` (or an awaitable of it): the host drives one
harness-evolution tick per task through the served decision and graph
ports; see ``meristem.harness.harness_cycle``."""


def clean_tool_name(prefix: str, server_name: str, original_tool_name: str) -> str:
    """The public name arbora serves a fleet tool under.

    Strips redundant server/module prefixes from the raw tool name and keeps the
    namespaced result within the 44-character budget (so a client-prefixed name
    stays <= 64 characters).
    """
    if server_name.startswith("systems-manager-mcp-"):
        base_server = "systems-manager-mcp"
    elif server_name.startswith("container-manager-mcp-"):
        base_server = "container-manager-mcp"
    else:
        base_server = server_name
    clean_server = base_server.replace("-", "_").lower()
    strips = [
        f"{clean_server}_mcp_",
        f"{clean_server}_",
        f"{prefix}_mcp_",
        f"{prefix}_",
    ]
    if base_server.endswith("-mcp"):
        mod_server = base_server[:-4].replace("-", "_").lower()
        strips.extend((f"{mod_server}_mcp_", f"{mod_server}_"))
    cleaned = original_tool_name
    for strip in strips:
        if cleaned.startswith(strip):
            cleaned = cleaned[len(strip) :]
            break
    candidate = f"{prefix}__{cleaned}"
    if len(candidate) > 44:
        budget = 44 - len(prefix) - 2
        candidate = f"{prefix}__{cleaned[:budget].strip('_')}"
    return candidate


class ScaffoldPublisher(Protocol):
    """Host-owned publication of an actual run-local scaffold revision."""

    async def publish(self, revision: int, items: tuple[PlanItem, ...]) -> None: ...


ScaffoldPublicationFactory = Callable[
    [Any], contextlib.AbstractAsyncContextManager[ScaffoldPublisher]
]
OptionalScaffoldPublicationFactory = Callable[
    [Any], contextlib.AbstractAsyncContextManager[ScaffoldPublisher | None]
]


class _ScopedPublisher:
    def __init__(self, publisher: ScaffoldPublisher) -> None:
        self.publisher = publisher
        self.active = True

    async def publish(self, revision: int, items: tuple[PlanItem, ...]) -> None:
        if not self.active:
            raise RuntimeError("scaffold publication scope is closed")
        try:
            await self.publisher.publish(revision, items)
        except Exception:
            raise RuntimeError("scaffold publication failed") from None


RunHistoryProvider = Callable[[Any], Awaitable[str | None]]


@dataclass(frozen=True, slots=True)
class CompletedChildBinding:
    """Host-authorized identity of a completed child's selected history."""

    child_id: str
    conversation_id: str
    predecessor_run_id: str
    history_revision: str


@dataclass(frozen=True, slots=True)
class RunCheckpointSelection:
    """One host-authorized read, not caller-supplied execution authority.

    The host binds run, caller and conversation to one consistent native read.
    Both fields are required: None means absent, not unavailable or unchecked.
    Completed history uses the existing version-one text envelope. Current-run
    content is opaque until the harness can restore it without replaying effects.
    """

    run_id: str
    current_run_checkpoint: str | None
    completed_history: str | None
    version: int = 1
    completed_child: CompletedChildBinding | None = None


RunCheckpointProvider = Callable[[Any], Awaitable[RunCheckpointSelection]]


class RunCheckpointPort(Protocol):
    """One host-owned checkpoint transaction for an admitted run.

    The harness only serializes and validates the bounded checkpoint payload.
    The host owns the durable snapshot, revision CAS and admission fencing.
    """

    admission_identity: str

    async def restore(self) -> str | None: ...

    async def save(self, content: str) -> None: ...


RunCheckpointFactory = Callable[
    [Any, Any], contextlib.AbstractAsyncContextManager[RunCheckpointPort | None]
]


@dataclass
class HostPorts:
    """The providers one host binds for the runs it starts.

    arbora builds this once at its composition root and sets it on every
    ``RunRequest``; each field is ``None`` until the host binds it, and an
    unbound binding port fails closed while an unbound read port reports the
    empty answer.
    """

    native: NativeToolsetProvider | None = None
    fleet: Callable[[], Mapping[str, FleetServer]] | None = None
    keepalive: Callable[[], contextlib.AbstractAsyncContextManager[None]] | None = None
    remote: RemoteToolsetProvider | None = None
    tool_client: ToolClientFactory | None = None
    credentials: OutboundCredentialProvider | None = None
    identity: DelegatedIdentityProvider | None = None
    decide: RunDecisionProvider | None = None
    caller: ToolCaller | None = None
    models: ModelProvider | None = None
    events: RunEventSink | None = None
    durable_run_events: DurableRunEventFactory | None = None
    active_child_input: ActiveChildInputFactory | None = None
    cycle: HarnessCycleProvider | None = None
    probe: HarnessProbeProvider | None = None
    scaffold_publication: OptionalScaffoldPublicationFactory | None = None
    run_history: RunHistoryProvider | None = None
    run_checkpoints: RunCheckpointProvider | None = None
    checkpoint_factory: RunCheckpointFactory | None = None

    def harness_probe_provider(self) -> HarnessProbeProvider | None:
        """Return the host probe executor; absence cannot authorize execution."""
        return self.probe

    def run_decision_provider(self) -> RunDecisionProvider | None:
        """The host's run-plan provider, or ``None`` (the harness then uses defaults)."""
        return self.decide

    def tool_caller(self) -> ToolCaller | None:
        """Return the host-bound dependency caller, or ``None`` without a host."""
        return self.caller

    def harness_cycle_provider(self) -> HarnessCycleProvider | None:
        """The host's cycle driver, or ``None`` (the tick then fails closed)."""
        return self.cycle

    def resolve_model(self, model_id: str) -> Any:
        """The host-built model for ``model_id``.

        Without a host provider the id passes through unchanged, so pydantic-ai
        resolves it itself (``"provider:name"`` ids need that provider's extra).
        """
        return model_id if self.models is None else self.models(model_id)

    def emit_run_event(self, event: Mapping[str, Any]) -> None:
        """Send one run event to the host sink; a no-op without a host."""
        if self.events is not None:
            self.events(event)

    @contextlib.asynccontextmanager
    async def active_child_input_scope(
        self, request: Any
    ) -> AsyncIterator[ActiveChildInputPort | None]:
        """Open one optional host-owned child input binding."""
        factory = self.active_child_input
        if factory is None:
            yield None
            return
        async with factory(request) as port:
            yield port

    @contextlib.asynccontextmanager
    async def durable_run_event_scope(
        self, request: Any
    ) -> AsyncIterator[DurableRunEventPublisher | None]:
        """Open the optional host-owned durable event port for one run."""
        factory = self.durable_run_events
        if factory is None:
            yield None
            return
        async with factory(request) as publisher:
            yield publisher

    @contextlib.asynccontextmanager
    async def authority_keepalive_scope(self) -> AsyncIterator[None]:
        """Hold the host's lease keepalive open; a no-op without a host."""
        if self.keepalive is None:
            yield
            return
        async with self.keepalive():
            yield

    def native_tool_names(self) -> frozenset[str]:
        """Names of the host's served operations available for native binding."""
        if self.native is None:
            return frozenset()
        return frozenset(str(name) for name in self.native.available_tools())

    def build_native_toolset(self, tool_names: list[str], *, toolset_id: str) -> Any:
        """Bind ``tool_names`` from the host; fail closed when no host is attached."""
        if self.native is None:
            raise RuntimeError(
                "native arbora toolset requires the arbora host to register a provider"
            )
        return self.native.build_toolset(list(tool_names), toolset_id)

    def build_url_toolset(
        self, url: str, *, toolset_id: str | None = None, timeout: float = 60.0
    ) -> Any:
        """The host-built client toolset for ``url``; fail closed without a host."""
        if self.remote is None:
            raise RuntimeError(
                "remote toolsets require the arbora host to register a provider"
            )
        return self.remote.build_url_toolset(str(url), toolset_id, float(timeout))

    def load_config_toolsets(self, config_path: str | Path) -> list[Any]:
        """The toolsets a client configuration file declares; fail closed without a host."""
        if self.remote is None:
            raise RuntimeError(
                "configured toolsets require the arbora host to register a provider"
            )
        return list(self.remote.load_config_toolsets(Path(config_path)))

    def open_tool_client(
        self, target: Any, *, timeout: float | None = None
    ) -> contextlib.AbstractAsyncContextManager[ToolClient]:
        """Open one host-provided tool client session; fail closed without a host."""
        if self.tool_client is None:
            raise RuntimeError(
                "tool client sessions require the arbora host to register a factory"
            )
        return self.tool_client(target, timeout=timeout)

    def outbound_auth(self) -> Any:
        """The host's refresh-capable outbound auth (``None`` without a host)."""
        return None if self.credentials is None else self.credentials.auth()

    def outbound_auth_header(self) -> dict[str, str]:
        """The host's current outbound ``Authorization`` header (empty without one)."""
        return {} if self.credentials is None else dict(self.credentials.header())

    def outbound_auth_status(self) -> dict[str, object]:
        """The host's outbound-credential configuration status."""
        if self.credentials is None:
            return {
                "mode": "host-unbound",
                "ready": False,
                "problems": ["no arbora host credential provider is registered"],
                "warnings": [],
            }
        return dict(self.credentials.status())

    def refresh_outbound_credentials(self) -> None:
        """Force the host to rotate its outbound bearer; fail closed without one."""
        if self.credentials is None:
            raise RuntimeError("outbound credential renewal requires the arbora host")
        self.credentials.refresh()

    def current_user_token(self) -> str | None:
        """The caller's user token as the host resolved it (``None`` without one)."""
        return None if self.identity is None else self.identity.user_token()

    def exchange_token_for_agent(self, label: str) -> str | None:
        """On-behalf-of exchange for ``label`` via the host (``None`` without one)."""
        if self.identity is None:
            return None
        return self.identity.exchange_for_agent(label)

    def fleet_catalog(self) -> dict[str, FleetServer]:
        """The host's ``{server_name: FleetServer}`` map (empty without a host)."""
        if self.fleet is None:
            return {}
        return {
            name.strip(): server
            for name, server in dict(self.fleet()).items()
            if isinstance(name, str)
            and name.strip()
            and isinstance(server, FleetServer)
        }

    @contextlib.asynccontextmanager
    async def scaffold_publication_scope(
        self, request: Any, state: PlanState | None
    ) -> AsyncIterator[ScaffoldPublisher | None]:
        """Publish actual state through a host binding, never a local read registry.

        The host owns verified identity, lease fencing, expiry and wire mapping.
        Missing providers make no availability claim. Failures may follow an external
        write; the accepted local revision is never rolled back.
        """
        factory = self.scaffold_publication
        if state is None or factory is None:
            yield None
            return
        try:
            scope = factory(request)
            bound = await scope.__aenter__()
            publisher = _ScopedPublisher(bound) if bound is not None else None
        except Exception:
            raise RuntimeError("scaffold publication binding failed") from None
        failure: BaseException | None = None
        try:
            if publisher is not None:
                await publisher.publish(*state.read_versioned())
            yield publisher
        except BaseException as exc:
            failure = exc
            raise
        finally:
            if publisher is not None:
                publisher.active = False
            try:
                await scope.__aexit__(
                    type(failure) if failure is not None else None,
                    failure,
                    failure.__traceback__ if failure is not None else None,
                )
            except Exception:
                if failure is None:
                    raise RuntimeError("scaffold publication close failed") from None

    def run_checkpoint_factory_bound(self) -> bool:
        """Return whether a host checkpoint transaction is available to this host."""
        return self.checkpoint_factory is not None

    @contextlib.asynccontextmanager
    async def run_checkpoint_scope(
        self, request: Any, plan: Any
    ) -> AsyncIterator[RunCheckpointPort | None]:
        """Open the optional host checkpoint transaction for one admitted run."""
        factory = self.checkpoint_factory
        if factory is None:
            yield None
            return
        async with factory(request, plan) as checkpoint:
            yield checkpoint
