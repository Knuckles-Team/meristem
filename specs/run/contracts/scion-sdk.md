# Connector SDK seam contract

Provider: scion-sdk SCION-CONNECTOR-CONTROL-R067
Consumer: meristem MERISTEM-RUN-R016

## Decisions

| Capability | Owner | Decision |
|---|---|---|
| Source access, cursors, conflict handling, mapping, certification, manifests and ontology pack generation | scion-sdk | Owned by the SDK. Meristem has no source connector package. |
| Connector toolkit and HTTP, credential and value helpers | scion-sdk | One copy in the SDK; connector repositories import the SDK, never meristem. |
| Document, conversation, feed, repository and database sources; enterprise source synchronization; vendor enrichment and write-back | scion-sdk | Owned by the SDK as runners and presets. |
| Connector import boundary | scion-sdk | A connector imports no meristem module and calls no native ingest; a census gate checks the fleet. |

## Allowed call direction

Connectors depend on the SDK only. Meristem submits typed agent claims through the client port and never reaches a source.

## Wire shape

No record crosses at runtime. The seam is an import boundary: a connector repository imports the connector SDK and never imports meristem, and meristem contains no connector, manifest, certification or ontology-pack code.

The census reads connector checkouts and flags any import of the retired meristem modules `agent_utilities.mcp`, `agent_utilities.core.config`, `agent_utilities.core.exceptions`, `agent_utilities.core.transport_security` and `agent_utilities.base_utilities` (`BANNED` in `scripts/check_connector_import_census.py`). Meristem reaches a fleet connector only through the host-bound `ToolCaller` (`meristem/host_ports.py`).

## Refusal codes

The census reports a `Finding` per connector checkout (`census(root)` in `scripts/check_connector_import_census.py`) and exits non-zero when a banned import is present. A tool step addressed to a connector that the host cannot call fails with `ToolCallRefused` or `ToolCallFailed` (`meristem/graph/state.py`).

## Authoritative types

- Meristem: `scripts/check_connector_import_census.py` (`BANNED`, `inspect_file`, `census`) and `meristem/host_ports.py` (`ToolCaller`).
- SDK: the connector toolkit, manifest and certification types named by the provider rows.

## Test vectors

- `tests/gates/test_connector_import_census.py` runs the census over fixture checkouts.
- `tests/gates/test_boundary_r011_source_connectors_deleted.py`, `test_boundary_r011_universal_connector_deleted.py`, `test_boundary_r011_manifest_deleted.py` and `test_boundary_r002_3_certification_oidc_deleted.py` assert the moved modules are absent.

## Implementing specs

| Side | Spec IDs |
|---|---|
| Provider | SCION-CONNECTOR-CONTROL-R067, SCION-CONNECTOR-CONTROL-R780.5, SCION-SOURCE-INGEST-R007, SCION-SOURCE-INGEST-R600 |
| Consumer | MERISTEM-RUN-R016 |
