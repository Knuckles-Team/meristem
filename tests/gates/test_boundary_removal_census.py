"""Removal proofs for the AU cut: the behavior lives with its owner, not here.

Each test asserts the moved code is gone from Meristem and that nothing in the
package or its tests still names it. The receiving owner of each moved capability is named in the contracts
under ``specs/run/contracts/``.
"""

import pytest
from _census import PACKAGE, ROOT, assert_absent, console_scripts, references


@pytest.mark.spec("MERISTEM-RUN-R016")
def test_deployment_validation_modules_do_not_import_certification_oidc() -> None:
    assert_absent("deployment")
    assert references(("certification_oidc",)) == []


@pytest.mark.spec("MERISTEM-RUN-R016")
def test_second_graph_projection_and_control_plane_shim_deleted() -> None:
    assert_absent("protocols/epistemic_operations", "control_plane")
    assert references(("epistemic_operations", "control_plane")) == []


@pytest.mark.spec("MERISTEM-RUN-R016")
def test_engine_bound_knowledge_graph_tree_is_deleted() -> None:
    """Ingestion commit, derivation, ontology, federation and retrieval left."""
    assert_absent("knowledge_graph", "assimilation", "etl", "streams")
    assert references(("meristem.knowledge_graph",), include_tests=True) == []


@pytest.mark.spec("MERISTEM-RUN-R016")
def test_source_and_enrichment_modules_moved_to_the_sdk() -> None:
    assert_absent(
        "ingestion",
        "automation",
        "sdd",
        "ecosystem",
        "enrichment",
        "knowledge_graph/enrichment",
        "knowledge_graph/kb",
        "knowledge_graph/extraction",
    )


@pytest.mark.spec("MERISTEM-RUN-R016")
def test_graph_schema_dtos_replaced_by_engine_types() -> None:
    assert_absent("models", "knowledge_graph/domain_packs")


@pytest.mark.spec("MERISTEM-RUN-R016")
def test_engine_duplicate_modules_and_worker_scripts_absent() -> None:
    from test_boundary_r018_r022_eg_duplicate_inventory import (  # type: ignore[import-not-found]
        INVENTORY,
        _matches,
    )

    for row in ("R018", "R019", "R020", "R021", "R022"):
        assert _matches(PACKAGE, INVENTORY[row]) == set()
    assert set(console_scripts()) == {"meristem"}


@pytest.mark.spec("MERISTEM-RUN-R016")
def test_infra_placement_modules_absent_without_callers() -> None:
    assert_absent("knowledge_graph/infra")
    assert (
        references(("placement_optimizer", "inventory_collector"), include_tests=True)
        == []
    )


@pytest.mark.spec("MERISTEM-RUN-R016")
def test_no_local_manifest_compiler_caller_remains() -> None:
    """The four former importers are gone, and none names the local compiler."""
    for script in (
        "generate_connector_manifests.py",
        "update_ontology_lock.py",
        "generate_native_connector_manifest.py",
    ):
        assert not (ROOT / "scripts" / script).exists()
    assert_absent(
        "knowledge_graph/domain_packs/pack_loader.py",
        "knowledge_graph/ontology/manifest_compiler.py",
    )
    assert references(("manifest_compiler",), include_tests=True) == []


DOMAIN_DIRECTORIES = [
    "domains/government",
    "domains/hr",
    "domains/law",
    "domains/medical",
    "models/domains",
    "ontology/shapes",
    "data",
    "protocols/voice_supply_chain",
]


@pytest.mark.spec("MERISTEM-RUN-R016")
@pytest.mark.parametrize("directory", DOMAIN_DIRECTORIES)
def test_domain_directory_is_gone(directory: str) -> None:
    assert_absent(directory)
    assert references((f"meristem.{directory.replace('/', '.')}",)) == []


@pytest.mark.spec("MERISTEM-RUN-R016")
def test_certification_oidc_is_named_by_no_script() -> None:
    for script in (
        "check_http_egress_boundary.py",
        "release/check_release_wheel.py",
        "security/check_http_egress_boundary_gate.py",
    ):
        assert not (ROOT / "scripts" / script).exists()
    naming = sorted(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "scripts").rglob("*")
        if path.is_file()
        and path.suffix in {".py", ".sh", ".md", ".yml"}
        and "certification_oidc" in path.read_text(encoding="utf-8", errors="replace")
    )
    assert naming == []


@pytest.mark.spec("MERISTEM-RUN-R016")
def test_usage_governance_memory_fleet_and_adapter_packages_are_gone() -> None:
    packages = [
        ("usage",),
        ("governance",),
        ("knowledge_graph/memory",),
        ("orchestration",),
        ("kvcache", "numeric", "caching", "media"),
    ]
    for paths in packages:
        assert_absent(*paths)
    dotted = tuple(
        f"meristem.{name}"
        for name in (
            "usage",
            "governance",
            "orchestration",
            "kvcache",
            "numeric",
            "caching",
            "media",
        )
    )
    assert references(dotted, include_tests=True) == []
    assert references(("knowledge_graph.memory",), include_tests=True) == []


@pytest.mark.spec("MERISTEM-RUN-R016")
def test_legacy_sparql_path_is_owned_by_the_engine_not_meristem() -> None:
    assert_absent("knowledge_graph/backends/sparql", "knowledge_graph/setup")
    assert "setup-databases" not in console_scripts()
    assert references(("stardog_sync", "sparql_ingestor"), include_tests=True) == []
