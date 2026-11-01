# Medulla seam contract

Provider: medulla MEDULLA-DECISION-ENGINE-R134
Consumer: meristem MERISTEM-RUN-R002

## Decisions

| Capability | Owner | Decision |
|---|---|---|
| Graph engine, graph compute, session and state facades | medulla | Reached only through the generated client. Meristem holds no second projection. |
| Durable work, queues, tenancy, topology and admission | medulla | Owned by medulla. |
| Reasoning, graph analytics, standardization and security checks | medulla | Owned by medulla. |
| Ingestion commit and derivation, ontology object model and emitters, graph schema types, federation, retrieval engines | medulla | Owned by medulla; schema types are generated, never hand-copied. |
| Usage facts | medulla | Stored as tenant-scoped records. Meristem emits usage events and keeps no local store. |
| Agent memory and the learning engine | medulla | Owned by medulla. |
| Domain models, shapes and data packs | medulla | Published as engine packs. Meristem keeps no domain content. |
| Choices (task selection, dependencies, plan, replay, study sources, field shielding, workflow registry, render receipt) | medulla | Medulla decides. Meristem applies the received decision, fails closed on an invalid one and decides nothing. |

## Allowed call direction

Meristem calls medulla through the generated client port the host binds. Medulla never calls meristem.

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
| Provider | MEDULLA-DECISION-ENGINE-R134, MEDULLA-DECISION-ENGINE-R803, MEDULLA-PLACEMENT-R001, MEDULLA-CONTRACT-R050, MEDULLA-CONTRACT-R051 |
| Consumer | MERISTEM-RUN-R001 to MERISTEM-RUN-R011, MERISTEM-RUN-R016 |
