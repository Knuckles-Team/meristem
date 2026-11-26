# MERISTEM-RUN — Data model

The records below are the types the run rows add. The row that defines each type is in `requirements.md`.

## Process control steps

- `RunTask.branch`, `RunTask.join`, `RunTask.decision` and `RunTask.approval` in `meristem/graph/state.py` (`MERISTEM-RUN-R018.1`): optional mappings that mirror the engine control steps. A task carries at most one, and never with a tool call. Unknown keys refuse at validation.
- `DecisionVerdict` in `meristem/host_ports.py` (`MERISTEM-RUN-R018.1`): `record_id`, `taken_task_ids`, `outputs` and `abstention`. The engine code of an abstention fails the step.
- `ApprovalOutcome` in the same file (`MERISTEM-RUN-R018.1`): `verdict` (`approve`, `reject` or `timeout`), `approver` and `decided_at_ms`.
- `DecisionPort` and `ApprovalWaiter` in the same file (`MERISTEM-RUN-R018.1`): the async callables the host binds through `HostPorts.decision` and `HostPorts.approval`.
- `DecisionPortUnavailable`, `ApprovalWaiterUnavailable`, `BranchVerdictInvalid`, `ApprovalRejected`, `ApprovalTimedOut` and `DecisionAbstained` in `meristem/graph/state.py` (`MERISTEM-RUN-R018.1` to `MERISTEM-RUN-R018.5`): the refusal classes of the control steps.
