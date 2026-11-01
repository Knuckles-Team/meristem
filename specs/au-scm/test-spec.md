# MERISTEM-SCM — Test specification

Governing spec: [spec.md](spec.md).

| Test ID | Requirement | Level | Setup and input | Expected observation | Evidence |
|---|---|---|---|---|---|
| T-001 | `MERISTEM-SCM-R001` | Served/integration | One served coding run. One edit to one file. | `source.commit` lands one changeset. `source.land` applies the landing policy. The run reports the changeset id. |  |
| T-002 | `MERISTEM-SCM-R001` | Integration | One clean submit. One overlapping second writer. | The first submit lands. The second returns `SOURCE_CONFLICT{paths}`. The loop rebases and retries. The retry lands. |  |
| T-003 | `MERISTEM-SCM-R001` | Unit | A conflict that keeps overlapping past the bound of 3 rebases. | The loop refuses with `conflict_retry_exhausted`. It submits no further changeset. |  |
| T-004 | `MERISTEM-SCM-R001` | Unit | No registered `ChangesetSubmitter` provider. | The port fails closed. The loop refuses before the first edit. |  |
| T-005 | `MERISTEM-SCM-R003` | Integration/unit | See `tests/harness/test_consumer_agent_discovery.py::test_discovery_emits_verified_facts`. | The row acceptance holds. |  |
| T-006 | `MERISTEM-SCM-R003.1` | Integration/unit | See `tests/harness/test_consumer_agent_discovery.py::test_discovery_emits_verified_facts`. | The row acceptance holds. |  |
| T-007 | `MERISTEM-SCM-R004` | Integration/unit | See `tests/harness/test_consumer_preplan_tests.py::test_receipt_submitted_before_planning`. | The row acceptance holds. |  |
| T-008 | `MERISTEM-SCM-R004.1` | Integration/unit | See `tests/harness/test_consumer_preplan_tests.py::test_receipt_submitted_before_planning`. | The row acceptance holds. |  |
| T-009 | `MERISTEM-SCM-R005` | Integration/unit | See `tests/harness/test_consumer_edit_prediction.py::test_edit_reverted_on_failed_prediction`. | The row acceptance holds. |  |
| T-010 | `MERISTEM-SCM-R005.1` | Integration/unit | See `tests/harness/test_consumer_edit_prediction.py::test_edit_reverted_on_failed_prediction`. | The row acceptance holds. |  |
| T-011 | `MERISTEM-SCM-R006` | Integration/unit | See `tests/run/test_verifier_receipt.py::test_each_case_is_retested_against_the_new_digest`. | The row acceptance holds. |  |
| T-012 | `MERISTEM-SCM-R006.1` | Integration/unit | See `tests/run/test_verifier_receipt.py::test_each_case_is_retested_against_the_new_digest`. | The row acceptance holds. |  |
| T-013 | `MERISTEM-SCM-R007` | Integration/unit | See `tests/harness/test_consumer_regression_test.py::test_test_runs_on_both_revisions`. | The row acceptance holds. |  |
| T-014 | `MERISTEM-SCM-R007.1` | Integration/unit | See `tests/harness/test_consumer_regression_test.py::test_test_runs_on_both_revisions`. | The row acceptance holds. |  |
| T-015 | `MERISTEM-SCM-R008` | Integration/unit | See `tests/test_host_ports.py::test_engine_decision_port_registration_and_absence`. | The row acceptance holds. |  |
| T-016 | `MERISTEM-SCM-R010` | Integration/unit | See `tests/harness/test_coding_toolset.py::test_execute_graph_runs_the_changeset_cycle_through_the_toolset`. | The row acceptance holds. |  |
| T-017 | `MERISTEM-SCM-R011.1` | Integration/unit | See `tests/harness/test_task_run.py::test_context_and_result_are_frozen_and_round_trip`. | The row acceptance holds. |  |
| T-018 | `MERISTEM-SCM-R011.2` | Integration/unit | See `tests/harness/test_task_run.py::test_one_cycle_per_task_and_incomplete_context_refuses`. | The row acceptance holds. |  |
| T-019 | `MERISTEM-SCM-R011.3` | Integration/unit | See `tests/harness/test_coding_toolset.py::test_execute_graph_reaches_run_task_run`. | The row acceptance holds. |  |
| T-020 | `MERISTEM-SCM-R011.4` | Integration/unit | See `tests/harness/test_task_run.py::test_each_failure_returns_code_stage_and_evidence`. | The row acceptance holds. |  |
| T-021 | `MERISTEM-SCM-R011.5` | Served | See `tests/harness/test_task_run_served.py::test_served_task_run_returns_result_and_evidence`. | The row acceptance holds. |  |
| T-022 | `MERISTEM-SCM-R012.1` | Unit | See `tests/harness/test_delivery_run.py::test_context_and_report_are_frozen_and_state_set_is_closed`. | The types are frozen. An unknown state refuses with `delivery_report_state_unknown`. |  |
| T-023 | `MERISTEM-SCM-R012.2` | Unit | See `tests/harness/test_delivery_run.py::test_inputs_apply_between_turns_in_receipt_order`. | Inputs apply between turns in receipt order. A late input refuses. |  |
| T-024 | `MERISTEM-SCM-R012.3` | Unit | See `tests/harness/test_delivery_run.py::test_ci_failure_revises_same_proposal_after_disarm`. | One cycle runs on the same proposal after the disarm verdict. A missing verdict pushes nothing. |  |
| T-025 | `MERISTEM-SCM-R012.4` | Unit | See `tests/harness/test_delivery_run.py::test_fixed_resolves_declined_replies_once_and_stop_verdict_halts`. | A fixed thread resolves. A declined one gets one reply. A stop verdict ends work on that reviewer. |  |
| T-026 | `MERISTEM-SCM-R012.5` | Unit | See `tests/harness/test_delivery_run.py::test_each_report_state_and_single_question`. | Each state reports. A report holds at most one question. |  |
| T-027 | `MERISTEM-SCM-R012.6` | Unit | See `tests/harness/test_delivery_run.py::test_recovery_note_skips_effects_that_already_landed`. | No landed effect repeats after a recovery note. |  |
| T-028 | `MERISTEM-SCM-R012.7` | Unit | See `tests/harness/test_delivery_run.py::test_background_limit_stops_jobs_and_reports_waiting`. | Jobs stop at the limit and the run reports `waiting`. |  |
| T-029 | `MERISTEM-SCM-R012.8` | Served | See `tests/harness/test_delivery_run_served.py::test_served_delivery_run_iterates_one_proposal`. | One revised changeset, one resolved thread and a `ready` report. |  |

## Negative and boundary cases

A failed fast gate must refuse the submit with zero effects. A `SOURCE_CONFLICT{paths}` refusal must list every conflicting path.
The loop must re-apply the edits only on the re-read head. The bound of 3 rebases must exhaust to a typed refusal.
A clean submit must write no second changeset for the same edits. A task run with an incomplete context must cause zero edits.

## Quality and release proof

Focused: `uv run --frozen python -m pytest tests/harness/test_changeset_loop.py tests/harness/test_task_run.py -q`.
Language-native: ruff, mypy and the repository scanners through `.config/pre-commit.yaml` and `release.yml`. A source test is not served acceptance.

## Symbol-level edits in the run loop

| Row | Proving test |
|---|---|
| `MERISTEM-SCM-R002` | `tests/harness/test_changeset_loop.py::test_symbol_edit_retries_once_on_stale_base` |
