# Arbora seam contract

Provider: arbora ARBORA-HOST-R007
Consumer: meristem MERISTEM-RUN-R001

## Decisions

| Capability | Owner | Decision |
|---|---|---|
| Public REST, MCP and A2A serving, gateway, multiplexer | arbora | Served only by arbora. Meristem hosts no server, A2A or ACP endpoint. |
| Deployment, installation, environment diagnostics, skill certification and the certification identity authority | arbora | Owned by arbora. Meristem has no `deployment/` package. |
| Fleet reconciliation, autoscaling, scaling authority and deploy watch | arbora | Owned by arbora. Meristem has no orchestration package. |
| Messaging adapters, request identity, readiness | arbora | Owned by arbora. |
| Process configuration | split | Connector base settings belong to the connector SDK. Meristem keeps only the harness settings in `core/config.py`, read through one environment reader. |
| Run execution | meristem | Meristem runs the loop. Arbora admits the run and passes the plan through host ports. |

## Allowed call direction

Arbora calls meristem only through `meristem.api` and the public host module set. Meristem never imports arbora; it receives each decision and checkpoint through the host ports bound for the run.

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
| Provider | ARBORA-HOST-R007, ARBORA-HOST-R011, ARBORA-HOST-R027.1 |
| Consumer | MERISTEM-RUN-R001, MERISTEM-RUN-R008, MERISTEM-RUN-R014, MERISTEM-RUN-R016 |
