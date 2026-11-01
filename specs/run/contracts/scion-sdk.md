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

No new wire record. The seam is an ownership boundary: the call direction above is the whole interface, and each owner exposes its capability through its own served operation or generated client.

## Refusal codes

The owner refuses with its own typed codes. Meristem maps an unknown or invalid reply to a refusal and never to a local fallback.

## Authoritative types

The owner repository holds the types; meristem imports the generated client and never copies a type.

## Test vectors

The removal census `tests/gates/test_boundary_removal_census.py` holds the absence checks on the meristem side; the owner specs hold the behaviour vectors.

## Implementing specs

| Side | Spec IDs |
|---|---|
| Provider | SCION-CONNECTOR-CONTROL-R067, SCION-CONNECTOR-CONTROL-R780.5, SCION-SOURCE-INGEST-R007, SCION-SOURCE-INGEST-R600 |
| Consumer | MERISTEM-RUN-R016 |
