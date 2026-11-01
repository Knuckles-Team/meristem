# Contract: task dispatch to one task run

Provider: medulla MEDULLA-SDLC-R015
Consumer: meristem MERISTEM-SCM-R011

The engine dispatch decision hands one task context to one task run. The harness returns one result.

## Wire shape

- Request: `TaskRunContext{task_id, row_id, row_text, plan_sections, contracts, data_model, code_neighbourhood, base_digest}`.
- Response: `TaskRunResult{status, changeset_id, evidence, failure}`. `failure` is `{code, stage, evidence}`.

## Refusal codes

- `task_context_incomplete`: the context has no row text or no base digest. The run makes zero edits.
- `gate_failed`, `conflict_retry_exhausted`, `submitter_unavailable`, `sandbox_unavailable`: typed task failures.

## Authoritative types

- `meristem/harness/task_run.py` (`TaskRunContext`, `TaskRunResult`).
- The medulla dispatch decision of `MEDULLA-SDLC-R015.3`.

## Test vectors

- `tests/harness/test_task_run.py` and `tests/harness/test_task_run_served.py`.
