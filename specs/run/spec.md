# MERISTEM-RUN — Agent run loop and ownership guards

Owner: meristem. Cross-repo IDs: MEDULLA-DECISION-ENGINE-R134, ARBORA-HOST-R007.
Public provenance: the rows of this spec and the contracts in [contracts/](contracts/) hold all required detail.

## Delivery mapping

Component: Agent run loop
Depends-on: MEDULLA-DECISION-ENGINE-R134, ARBORA-HOST-R007
Code: ../../meristem/graph/executor.py ../../meristem/rlm/runner.py ../../meristem/harness/plan.py

## User Scenarios & Testing

### User Story 1 - Run a task through the host (Priority: P1)

A developer runs a task through arbora. Meristem executes the run: it applies the received plan, enforces the received limits and budget, publishes events through host ports and resumes from a host checkpoint. The operator sees each step, its cost and why a run stopped.

**Acceptance Scenarios**:

1. **Given** a host plan, **When** the run starts, **Then** the plan sets the model, tools and limits and the run stops at its budget.
2. **Given** no host decision port, **When** the run starts, **Then** it uses the documented fallback plan or fails closed.
3. **Given** an invalid received decision or receipt, **When** the run starts, **Then** it refuses with a typed code and zero model requests.

### User Story 2 - Keep ownership where it belongs (Priority: P2)

A maintainer adds code to the repository. The ownership guards reject a module under another owner's path, an unlisted package directory, a second configuration reader and any module released to another owner.

**Acceptance Scenarios**:

1. **Given** a new module outside the owner registry, **When** the guard runs, **Then** it fails and names the module.
2. **Given** a released capability, **When** the removal census runs, **Then** the package, import and script are absent.

### Edge Cases

- A binding that cannot resolve fails the step with a typed failure and no model retry.
- A missing host port never silently falls back to a local store.

## Requirements

- **FR-001**: The harness applies the received plan, limits, checkpoint and events through host ports and decides nothing. Rows: `MERISTEM-RUN-R001`.
- **FR-002**: The harness consumes the engine decisions it receives and refuses an invalid one. Rows: `MERISTEM-RUN-R002`, `MERISTEM-RUN-R003`, `MERISTEM-RUN-R004`, `MERISTEM-RUN-R005`, `MERISTEM-RUN-R006`, `MERISTEM-RUN-R007`, `MERISTEM-RUN-R009`, `MERISTEM-RUN-R010`, `MERISTEM-RUN-R011`.
- **FR-003**: The ownership guards reject drift from the recorded owners. Rows: `MERISTEM-RUN-R008`, `MERISTEM-RUN-R012`, `MERISTEM-RUN-R013`, `MERISTEM-RUN-R014`, `MERISTEM-RUN-R015`, `MERISTEM-RUN-R016`.

## Success Criteria

- **SC-001**: Every row has a passing bound test at the merged head. Rows: `MERISTEM-RUN-R001`, `MERISTEM-RUN-R016`.

## Why a new spec

Search: `git grep -n "run loop" -- specs` found the run-loop rows only in the former boundary spec, whose remaining content was ownership decisions. No other spec owns the in-run harness loop, its host ports or the ownership guards, so they live here. The ownership decisions moved to the contracts.

## Assumptions

- The host binds every port a run needs; a missing port is a refusal, not a local default.

## Scope and interfaces

Owned: the run loop, host ports, scaffold, dependency execution, history and checkpoint consumption, and the guards. Contracts: [arbora](contracts/arbora.md), [medulla](contracts/medulla.md), [scion-sdk](contracts/scion-sdk.md), [repository-manager](contracts/repository-manager.md). Non-goals: any store, planner, selector, router, retriever, policy or served surface.

## Traceability

| Label | Rows | Design section | Test ID | Evidence |
|---|---|---|---|---|
| FR-001 | `MERISTEM-RUN-R001` | plan.md Production caller chain | T001 |  |
