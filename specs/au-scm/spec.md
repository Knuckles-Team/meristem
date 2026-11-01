# MERISTEM-SCM — Development loop on changesets

Owner: meristem. Cross-repo IDs: MEDULLA-SCM-R002, MEDULLA-SCM-R003, MEDULLA-SDLC-R015, ARBORA-OPS-R848, ARBORA-OPS-R849, ARBORA-OPS-R896. Delivery assignment runs consume `MEDULLA-SDLC-R019` and `ARBORA-OPS-R896.7` to `ARBORA-OPS-R896.12`.
Public provenance: the rows of this spec and the linked owner specs hold all required detail.

## Delivery mapping

Component: Coding harness
Depends-on: MEDULLA-SCM-R002, MEDULLA-SCM-R003, ARBORA-OPS-R848, ARBORA-OPS-R849
References: [MEDULLA-SDLC](https://github.com/Knuckles-Team/medulla/blob/main/specs/development-lifecycle/spec.md), [ARBORA-OPS](https://github.com/Knuckles-Team/arbora/blob/main/specs/hosted-api-operations/spec.md)
Code: ../../meristem/harness/changeset_loop.py ../../meristem/harness/edit_engine.py ../../meristem/host_ports.py

## User Scenarios & Testing

### User Story 1 - Develop a repository on changesets (Priority: P1)

A developer runs a coding task through arbora. The harness edits in a virtual workspace and submits changesets without local git. The operator sees which changeset landed and why a conflict retried.

**Acceptance Scenarios**:

1. **Given** one edit to one file, **When** the run submits, **Then** one changeset lands and the run reports its id.
2. **Given** an overlapping second writer, **When** the submit returns `SOURCE_CONFLICT{paths}`, **Then** the loop rebases on the new head and retries.
3. **Given** a failed fast gate, **When** the run reaches the submit step, **Then** the loop refuses with zero effects.

### User Story 2 - Apply received decisions (Priority: P2)

The harness applies decisions it receives from the engine and decides nothing itself.

**Acceptance Scenarios**:

1. **Given** a received agent plan, **When** the coding step starts, **Then** the harness applies the plan. It holds no default except when no decision arrives.

### User Story 3 - Execute one task run (Priority: P3)

An orchestrated implementation run hands the harness one task with its context. The harness returns the result and the evidence.

**Acceptance Scenarios**:

1. **Given** a task context with row text and a base digest, **When** the run starts, **Then** the harness runs one cycle and returns the changeset id.
2. **Given** a failing gate, **When** the task run ends, **Then** the harness returns a typed failure with code, stage and evidence.

### User Story 4 - Iterate one delivery on received feedback (Priority: P2)

The platform delivers a spec row as a change proposal. CI results and review findings arrive later. The harness revises the same proposal until it is ready, and it never merges.

**Acceptance Scenarios**:

1. **Given** a delivery with a failed CI input, **When** the assignment runs, **Then** the harness asks for the arming verdict and revises the same proposal once.
2. **Given** a fixed review finding, **When** the revision lands on the proposal, **Then** the harness resolves that thread and reports `ready`.
3. **Given** a recovery note, **When** the assignment starts, **Then** the harness reads the proposal first and repeats no landed effect.

### Edge Cases

- A conflict that keeps overlapping past 3 rebases refuses with `conflict_retry_exhausted`.
- No registered `ChangesetSubmitter` provider makes the port fail closed before the first edit.
- A task context without row text or base digest refuses with `task_context_incomplete` and causes zero edits.
- A delivery revision without a received arming verdict refuses with `delivery_arming_verdict_missing` and pushes nothing.
- An input after a terminal report refuses with `delivery_input_after_terminal`.

## Requirements

- **FR-001**: The harness must run the edit, gate, submit and rebase loop for each run. Rows: `MERISTEM-SCM-R001`, `MERISTEM-SCM-R002`, `MERISTEM-SCM-R010`.
- **FR-002**: The harness must apply received medulla decisions and decide nothing. Rows: `MERISTEM-SCM-R003`, `MERISTEM-SCM-R004`, `MERISTEM-SCM-R005`, `MERISTEM-SCM-R006`, `MERISTEM-SCM-R007`, `MERISTEM-SCM-R008`.
- **FR-003**: The harness must execute one task run with the received context and return result and evidence. Rows: `MERISTEM-SCM-R011`, `MERISTEM-SCM-R011.1`, `MERISTEM-SCM-R011.2`, `MERISTEM-SCM-R011.3`, `MERISTEM-SCM-R011.4`, `MERISTEM-SCM-R011.5`.
- **FR-004**: The harness must run one delivery assignment that applies received feedback to the same proposal and returns one report. Rows: `MERISTEM-SCM-R012`, `MERISTEM-SCM-R012.1`, `MERISTEM-SCM-R012.2`, `MERISTEM-SCM-R012.3`, `MERISTEM-SCM-R012.4`, `MERISTEM-SCM-R012.5`, `MERISTEM-SCM-R012.6`, `MERISTEM-SCM-R012.7`.

## Success Criteria

- **SC-001**: A served coding run lands one changeset and reports its id. Rows: `MERISTEM-SCM-R001`.
- **SC-002**: A served task run returns the changeset id and evidence, or a typed failure. Rows: `MERISTEM-SCM-R011.5`.
- **SC-003**: A served delivery run revises one proposal, resolves one thread and pushes zero times without an arming verdict. Rows: `MERISTEM-SCM-R012.8`.

## Assumptions

- The head-read and workspace interfaces follow the owner specs `MEDULLA-SCM-R003` and `ARBORA-OPS-R848`.
- The host must bind and verify an isolated workspace before the loop runs its fast gates. A local backend proves control flow only.
- `ARBORA-OPS-R849` requires the server to run its gates on the changeset tree before landing. A caller-selected command cannot satisfy it.

## Why a new spec

The cross-repo contracts are prerequisites, not evidence of delivery. `MEDULLA-SCM-R002` specifies the changeset record. `MEDULLA-SCM-R003` specifies acceptance and refusal. `ARBORA-OPS-R848` and `ARBORA-OPS-R849` specify the operations and the landing policy. No spec owns the in-run loop that drives them. The spec `MERISTEM-HARNESS-CYCLE` owns the evolution cycle, not this loop.

## Scope and interfaces

Owned: the changeset development loop (`meristem/harness/changeset_loop.py`), the consumer rows for received decisions, the task run and the delivery assignment run (`meristem/harness/delivery_run.py`). Edits apply through `apply_edits` (`meristem/harness/edit_engine.py:457`). The fast gates run in the `DevWorkspace` sandbox (`meristem/runtime/workspace.py:103`). Submission runs through the `ChangesetSubmitter` host port. The port fails closed without a provider. Non-goals: the blob store and changeset log (medulla). Also out of scope: the served operations and landing policy (arbora), the connector tools and any local git repository. The delivery records, the stage, the round budget and the arming verdict belong to medulla. Merging belongs to the owner alone.

## Traceability

| Label | Rows | Design section | Test ID | Evidence |
|---|---|---|---|---|
| FR-001 | `MERISTEM-SCM-R001` | plan.md#architecture | T-001 |  |
| FR-002 | `MERISTEM-SCM-R003` | plan.md#consumer-rows-for-medulla-decisions | T-005 |  |
| FR-003 | `MERISTEM-SCM-R011` | plan.md#task-run | T-017 |  |
| SC-001 | `MERISTEM-SCM-R001` | plan.md#live-integration-path | T-001 |  |
| FR-004 | `MERISTEM-SCM-R012` | plan.md#delivery-assignment-run | T-022 |  |
| SC-002 | `MERISTEM-SCM-R011.5` | plan.md#task-run | T-021 |  |
| SC-003 | `MERISTEM-SCM-R012.8` | plan.md#delivery-assignment-run | T-029 |  |
