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

No wire record of its own. Meristem receives medulla decisions as values inside the run plan and through the tool caller; it computes none of them.

| Decision | Medulla row | Where meristem applies it |
|---|---|---|
| Dependency plan: tasks, dependencies, output task, limits, tool steps and bindings | `MEDULLA-DECISION-ENGINE-R134`, `R134.111.8` to `R134.111.13` | `RunPlan.dependency_plan` (`DependencyPlan`, `RunTask`, `ToolStep` in `meristem/graph/state.py`), validated by `validate_dependency_plan` and run by `iterate_dependency_plan` in `meristem/graph/dependency.py` |
| Run budget, clamped to the tenant ceiling | `MEDULLA-DECISION-ENGINE-R144.7` | `RunPlan.budget` (`RunBudget`) passed to the engine port unchanged |
| Render receipt | `MEDULLA-TYPED-PACKS-R002.10` | `RunPlan.render_receipt` checked by `check_render_receipt` in `meristem/graph/render_receipt.py` |
| Durable checkpoint | `MEDULLA-DURABLE-KERNEL-R028.1` | `RunCheckpointPort` in `meristem/host_ports.py` and `RunCheckpoint` in `meristem/graph/checkpoint.py` |
| Retrieval round and Discover verdict | `MEDULLA-FEDERATED-QUERY-R081.11`, `R082.3`, `R083.3`, `R089` | applied by the retrieval step rows `MERISTEM-RUN-R010` and `R011` |
| Study sources, field shielding, workflow registry, replay events | `MEDULLA-DECISION-ENGINE-R139.100`, `R163.2`, `R162.3`, `R166.3`, `R088.2.1` | consumer rows `MERISTEM-RUN-R003` to `R006` |

## Refusal codes

Medulla refuses with its own typed codes. Meristem reports these refusals without a fallback:

- `BindingUnresolved`, `ToolCallRefused`, `ToolCallFailed`: a tool step whose binding does not resolve or whose caller refuses or fails; dependents never start.
- `RenderReceiptRefused` (six codes listed in `meristem/graph/render_receipt.py`): zero model requests.
- `STUDY_SOURCE_FIELD_MISSING`, `STUDY_SOURCE_DIGEST_MISMATCH`, `WORKSPACE_PATH_WITHHELD`, `WORKFLOW_UNKNOWN`, `WORKFLOW_DIGEST_MISMATCH`, `REPLAY_ENVELOPE_MALFORMED`, `REPLAY_SEQUENCE_GAP`, `REPLAY_OUT_OF_ORDER`, `REPLAY_DUPLICATE_CONFLICT`: the planned consumer refusals of rows `R003.1`, `R004.1`, `R005.1` and `R006.1`; those modules are not yet in the tree.

## Authoritative types

- Meristem: `meristem/graph/state.py`, `meristem/graph/dependency.py`, `meristem/graph/render_receipt.py`, `meristem/graph/checkpoint.py`, `meristem/host_ports.py`.
- Medulla: the generated client and the engine rows named in the table; meristem copies none of those types.

## Test vectors

- `tests/test_dependency.py` and `tests/test_checkpoint.py` run the dependency-plan and checkpoint vectors; `tests/graph/test_render_receipt_consumer.py` runs the receipt vectors against `tests/fixtures/medulla/render_receipt.json`.
- `tests/gates/test_boundary_removal_census.py` and `tests/test_retired_quantum_security_standardization.py` hold the absence checks for the capabilities medulla owns.

## Implementing specs

| Side | Spec IDs |
|---|---|
| Provider | MEDULLA-DECISION-ENGINE-R134, MEDULLA-DECISION-ENGINE-R803, MEDULLA-PLACEMENT-R001, MEDULLA-CONTRACT-R050, MEDULLA-CONTRACT-R051 |
| Consumer | MERISTEM-RUN-R001 to MERISTEM-RUN-R011, MERISTEM-RUN-R016 |
