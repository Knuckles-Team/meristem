"""Host ports: fail closed without a host, delegate once one is registered."""

from __future__ import annotations

import contextlib

import ports_support as support
import pytest

from meristem import host_ports as hp


@pytest.fixture(autouse=True)
def _no_host():
    def clear():
        support.register_native_toolset_provider(None)
        support.register_fleet_catalog_provider(None)
        support.register_authority_keepalive(None)
        support.register_remote_toolset_provider(None)
        support.register_tool_client_factory(None)
        support.register_outbound_credential_provider(None)
        support.register_delegated_identity_provider(None)

    clear()
    yield
    clear()


@pytest.mark.parametrize(
    "call",
    [
        lambda: support.PORTS.build_native_toolset(["a"], toolset_id="t"),
        lambda: support.PORTS.build_url_toolset("http://x"),
        lambda: support.PORTS.load_config_toolsets("cfg.json"),
        lambda: support.PORTS.open_tool_client("http://x"),
        support.PORTS.refresh_outbound_credentials,
    ],
)
def test_binding_ports_fail_closed(call):
    with pytest.raises(RuntimeError, match="arbora"):
        call()


async def test_read_ports_report_empty_without_host():
    assert support.PORTS.native_tool_names() == frozenset()
    assert support.PORTS.fleet_catalog() == {}
    assert (
        support.PORTS.outbound_auth() is None
        and support.PORTS.outbound_auth_header() == {}
    )
    assert support.PORTS.outbound_auth_status()["ready"] is False
    assert (
        support.PORTS.current_user_token() is None
        and support.PORTS.exchange_token_for_agent("x") is None
    )
    async with support.PORTS.authority_keepalive_scope():
        pass


async def test_registered_host_is_used():
    entered: list[str] = []

    @contextlib.asynccontextmanager
    async def keepalive():
        entered.append("in")
        yield

    support.register_native_toolset_provider(
        hp.NativeToolsetProvider(
            available_tools=lambda: ["go_a"],
            build_toolset=lambda names, tid: (names, tid),
        )
    )
    support.register_fleet_catalog_provider(
        lambda: {" s ": hp.FleetServer(prefix="s"), "bad": object()}
    )
    support.register_authority_keepalive(keepalive)
    support.register_delegated_identity_provider(
        hp.DelegatedIdentityProvider(
            user_token=lambda: "u", exchange_for_agent=lambda label: f"t-{label}"
        )
    )
    assert support.PORTS.native_tool_names() == frozenset({"go_a"})
    assert support.PORTS.build_native_toolset(["go_a"], toolset_id="t") == (
        ["go_a"],
        "t",
    )
    assert list(support.PORTS.fleet_catalog()) == ["s"]
    assert (
        support.PORTS.current_user_token(),
        support.PORTS.exchange_token_for_agent("x"),
    ) == ("u", "t-x")
    async with support.PORTS.authority_keepalive_scope():
        pass
    assert entered == ["in"]


@pytest.mark.parametrize(
    ("prefix", "server", "tool", "expected"),
    [
        ("gl", "gitlab-api", "gitlab_api_get_project", "gl__get_project"),
        ("cm", "container-manager-mcp-r820", "container_manager_list", "cm__list"),
        ("x", "srv", "srv_" + "a" * 60, "x__" + "a" * 41),
    ],
)
def test_clean_tool_name(prefix, server, tool, expected):
    assert hp.clean_tool_name(prefix, server, tool) == expected


@pytest.mark.spec("MERISTEM-RUN-R001.5.2")
async def test_scaffold_publisher_is_inactive_before_host_close():
    from contextlib import asynccontextmanager

    from meristem.host_ports import PlanState

    retained = []
    published = []

    class Publisher:
        async def publish(self, revision, items):
            published.append(revision)

    @asynccontextmanager
    async def factory(request):
        yield Publisher()
        with pytest.raises(RuntimeError, match="scope is closed"):
            await retained[0].publish(2, ())

    support.register_scaffold_publication_factory(factory)
    async with support.PORTS.scaffold_publication_scope(
        object(), PlanState()
    ) as publisher:
        retained.append(publisher)
        await publisher.publish(1, ())
    with pytest.raises(RuntimeError, match="scope is closed"):
        await retained[0].publish(3, ())
    assert published == [0, 1]


@pytest.mark.spec("MERISTEM-RUN-R001.5.2")
@pytest.mark.parametrize("bound,enabled", [(False, True), (True, False)])
async def test_absent_or_disabled_scaffold_has_no_publication_binding(bound, enabled):
    from meristem.host_ports import PlanState

    def factory(request):
        pytest.fail("disabled scaffold must not bind a publisher")

    support.register_scaffold_publication_factory(factory if bound else None)
    async with support.PORTS.scaffold_publication_scope(
        object(), PlanState() if enabled else None
    ) as publisher:
        assert publisher is None


@pytest.mark.spec("MERISTEM-RUN-R001.5.2")
async def test_cancellation_during_publication_closes_host_scope():
    import asyncio
    from contextlib import asynccontextmanager

    from meristem.host_ports import PlanState

    entered = asyncio.Event()
    closed = []

    class Publisher:
        async def publish(self, revision, items):
            entered.set()
            await asyncio.Future()

    @asynccontextmanager
    async def factory(request):
        try:
            yield Publisher()
        finally:
            closed.append(True)

    async def publish():
        async with support.PORTS.scaffold_publication_scope(object(), PlanState()):
            pytest.fail("initial publication cannot complete")

    support.register_scaffold_publication_factory(factory)
    task = asyncio.create_task(publish())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed == [True]


@pytest.mark.spec("MERISTEM-RUN-R001.5.2")
@pytest.mark.parametrize("cancelled", [False, True])
async def test_host_close_failure_preserves_body_failure(cancelled):
    import asyncio
    from contextlib import asynccontextmanager

    from meristem.host_ports import PlanState

    class Publisher:
        async def publish(self, revision, items):
            pass

    @asynccontextmanager
    async def factory(request):
        try:
            yield Publisher()
        finally:
            raise ValueError("private-close-details")

    support.register_scaffold_publication_factory(factory)
    failure = asyncio.CancelledError() if cancelled else LookupError("original")
    with pytest.raises(type(failure)) as caught:
        async with support.PORTS.scaffold_publication_scope(object(), PlanState()):
            raise failure
    assert caught.value is failure


@pytest.mark.spec("MERISTEM-RUN-R001.5.2")
async def test_failed_publication_keeps_accepted_revision():
    from contextlib import asynccontextmanager

    from meristem.host_ports import PlanItem, PlanState

    class Publisher:
        async def publish(self, revision, items):
            if revision:
                raise ValueError("uncertain external outcome")

    @asynccontextmanager
    async def factory(request):
        yield Publisher()

    support.register_scaffold_publication_factory(factory)
    state = PlanState()
    accepted = (PlanItem(text="accepted", status="done"),)
    with pytest.raises(RuntimeError, match="scaffold publication failed"):
        async with support.PORTS.scaffold_publication_scope(
            object(), state
        ) as publisher:
            revision, items = state.replace_versioned(accepted)
            await publisher.publish(revision, items)
    assert state.read_versioned() == (1, accepted)


@pytest.mark.spec("MERISTEM-RUN-R001.5.2")
@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
async def test_optional_host_binding_can_decline_one_run_and_still_close(outcome):
    import asyncio
    from contextlib import asynccontextmanager, nullcontext

    from meristem.host_ports import PlanItem, PlanState

    closed = []

    @asynccontextmanager
    async def factory(request):
        try:
            yield None
        finally:
            closed.append(request)

    support.register_optional_scaffold_publication_factory(factory)
    state = PlanState()
    request = object()
    failure = {"error": LookupError, "cancel": asyncio.CancelledError}.get(outcome)
    expected = pytest.raises(failure) if failure else nullcontext()
    with expected:
        async with support.PORTS.scaffold_publication_scope(
            request, state
        ) as publisher:
            assert publisher is None
            state.replace_versioned((PlanItem(text="local", status="pending"),))
            if failure:
                raise failure()
    assert closed == [request]
    assert state.read_versioned()[0] == 1
