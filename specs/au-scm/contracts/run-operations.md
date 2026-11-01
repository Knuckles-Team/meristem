# Contract: run start and run observe

Provider: arbora ARBORA-OPS-R896.3
Consumer: meristem MERISTEM-SCM-R011.3

Arbora starts an implementation run and observes it. The harness carries out the run it receives.

## Wire shape

- `development.run.start{repository, phase, task_id?, idempotency_key}` returns `{run_id}`.
- `development.run.status{run_id}` (ARBORA-OPS-R896.4) returns the run state, the task lines done and open, the evidence digests and the gate verdicts.

## Refusal codes

- `PROJECT_MODE_REFERENCE`: the repository is in mode `reference`.
- `RUN_NOT_FOUND`: the run ID is unknown to the caller tenant.

## Authoritative types

- `arbora/api/ops/development.py` (`handle_run_start`, `handle_run_status`).
- `meristem/graph/runner.py::execute_graph`.

## Test vectors

- `tests/harness/test_task_run_served.py`.
