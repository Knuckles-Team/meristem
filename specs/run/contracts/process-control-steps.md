# Seam: process control steps in a run

Provider: medulla `MEDULLA-DECISION-ENGINE-R134.111.14` (control step model) and `MEDULLA-DECISION-ENGINE-R180.4` (`BranchSelect` and `DecisionTableEval`).
Consumer: meristem `MERISTEM-RUN-R018` applies the steps; arbora `ARBORA-OPS-R898` binds the ports.

## Wire shape

The harness receives `BranchSpec`, `JoinSpec`, `DecisionSpec` and `ApprovalSpec` inside the canonical plan. It sends `BranchSelect` with the plan digest and the branch task id through the host `DecisionPort`. It sends `DecisionTableEval` with the decision IRI and the bound inputs through the same port. Arbora registers `DecisionPort` and `ApprovalWaiter` before `iterate_run`; the waiter resolves through `process.approval.decide`.

## Refusal codes

Harness: `DecisionPortUnavailable`, `ApprovalWaiterUnavailable`, `BranchVerdictInvalid`, `ApprovalRejected`, `ApprovalTimedOut`. Engine codes such as `BRANCH_NO_ARM` and `DMN_NO_RULE_MATCHED` fail the step.

## Authoritative types

Engine control steps in the medulla task plan; `RunTask` control fields, `DecisionPort` and `ApprovalWaiter` in `meristem/graph/state.py` and `meristem/host_ports.py`.

## Test vectors

The tests named by the `MERISTEM-RUN-R018` rows in `test-spec.md` hold the vectors.
