# Contract: changeset acceptance and landing policy

Provider: medulla MEDULLA-SCM-R003
Consumer: meristem MERISTEM-SCM-R001

The harness submits changesets. The engine accepts or refuses them. Arbora applies the landing policy (ARBORA-OPS-R849).

## Wire shape

- `source.commit{repo, branch, base_head, message, paths, trailers, run_trace_id}` returns `{changeset_id, head}`.
- `source.land{changeset_id}` returns the landing verdict.

## Refusal codes

- `SOURCE_CONFLICT{paths}`: the path set overlaps a newer head. The harness rebases and retries up to 3 times.
- `LANDING_GATE_BLOCKED`: a required gate verdict failed or is missing.

## Authoritative types

- `meristem/harness/changeset_loop.py` (`CycleResult`).
- The medulla changeset record of `MEDULLA-SCM-R003`.

## Test vectors

- `tests/harness/test_changeset_loop.py`.
