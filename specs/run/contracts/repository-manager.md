# Repository manager seam contract

Provider: repository-manager RM-GOVERNANCE-R001
Consumer: meristem MERISTEM-RUN-R016

## Decisions

| Capability | Owner | Decision |
|---|---|---|
| Merge-queue sequencing, concept reservation and development governance | repository-manager | Owned by repository-manager. Meristem has no governance package. |
| Repository and worktree operations | repository-manager | Owned by repository-manager. |

## Allowed call direction

Meristem and arbora reach repository operations through repository-manager operations. Repository-manager does not call meristem.

## Wire shape

No record crosses at runtime. Meristem holds no merge-queue, concept-reservation or worktree code and calls no repository-manager operation; repository-manager does not call meristem. The seam is the absence of the `governance/` modules from meristem, recorded as a relocation in `specs/run/plan.md` and checked by the relocation census.

## Refusal codes

None. No call crosses the seam, so no refusal is defined. A reappearance of a moved path fails the census assertion in `tests/gates/test_boundary_r036_r047_relocation_census.py` and the governance import check below.

## Authoritative types

- Meristem: the relocation inventory in `tests/gates/test_boundary_r036_r047_relocation_census.py` (the `governance/` concept modules among the paths it records).
- Repository-manager: the governance operations named by the provider row.

## Test vectors

- `tests/gates/test_boundary_r036_r047_relocation_census.py` and `tests/gates/test_boundary_r024_governance_import_moved.py` hold the absence vectors.
- `tests/gates/test_boundary_removal_census.py` covers the removed console scripts and modules.

## Implementing specs

| Side | Spec IDs |
|---|---|
| Consumer | MERISTEM-RUN-R016 |
