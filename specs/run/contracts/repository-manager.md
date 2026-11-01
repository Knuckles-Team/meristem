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
| Consumer | MERISTEM-RUN-R016 |
