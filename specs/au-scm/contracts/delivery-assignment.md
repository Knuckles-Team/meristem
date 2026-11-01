# Contract: delivery assignment inputs, verdicts and reports

Provider: arbora ARBORA-OPS-R896.12
Consumer: meristem MERISTEM-SCM-R012

Arbora starts one delivery assignment and delivers its inputs. Medulla owns the delivery records and verdicts (MEDULLA-SDLC-R019). The harness applies them and returns one report per turn.

## Wire shape

- Start: `DeliveryAssignmentContext{delivery_id, scope_rows, proposal_ref, inputs, recovery_note, round_verdicts, background_limit_s}`.
- Input: one active-child message per feedback event, with `{input_id, kind, source_event_id, body}`. `kind` is `ci`, `review`, `review_thread` or `person`.
- Arming verdict (MEDULLA-SDLC-R019.10, applied by ARBORA-OPS-R896.10): `{verdict: arm|disarm|hold, head}`.
- Round verdict (MEDULLA-SDLC-R019.9): `{reviewer, verdict: continue|stop_addressing}`.
- Report (MEDULLA-SDLC-R019.7, read by ARBORA-OPS-R896.7): `DeliveryReportOut{state, summary, proposal_ref, question}`.

## Refusal codes

- `delivery_report_state_unknown`: the report state is outside the six states.
- `delivery_input_after_terminal`: an input arrived after a terminal report.
- `delivery_arming_verdict_missing`: no arming verdict arrived before a push. Nothing is pushed.
- `delivery_disposition_refused`: the host refused a thread action.
- `delivery_recovery_read_failed`: the recovery read failed. The run reports `blocked`.
- `delivery_background_limit_invalid`: the received limit is not positive.

## Authoritative types

- `meristem/harness/delivery_run.py` (`DeliveryAssignmentContext`, `DeliveryReportOut`).
- The medulla delivery records of MEDULLA-SDLC-R019.2.
- `arbora/api/ops/development.py` delivery handlers of ARBORA-OPS-R896.7.

## Test vectors

- `tests/harness/test_delivery_run.py` and `tests/harness/test_delivery_run_served.py`.
