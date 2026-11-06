# MERISTEM-HARNESS-CYCLE — Data model

The records below are the types the in-run state machines add. Files named `new` do not exist on the default branch. The row that defines each type is in `requirements.md`.

## Explicit state machines

- `RunStatus` in `meristem/graph/state.py` (`MERISTEM-HARNESS-CYCLE-R084.1`): the closed enum `PENDING`, `RUNNING`, `CANCELLING`, `COMPLETED`, `FAILED`, `STOPPED_REQUESTED`, `STOPPED_BUDGET` and `STOPPED_CANCELLED`. It replaces the string literal of the same name. `stop_reason` and `is_terminal` are derived.
- `RunEvent` in the same file: `start`, `finish`, `stop_requested`, `stop_budget`, `cancel`, `drain_complete` and `fail`.
- `RunTransitionRefused` in the same file (`MERISTEM-HARNESS-CYCLE-R084.2`): `machine`, `from_status`, `event` and `reason`. The code is `RUN_TRANSITION_REFUSED`.
- `TaskStatus` and `TaskEvent` in the same file (`MERISTEM-HARNESS-CYCLE-R085.1`): the task enums of the fan-out machine.
- `TaskTransitionRefused` in the same file (`MERISTEM-HARNESS-CYCLE-R085.2`): the same four fields with `machine` set to `task`. The code is `TASK_TRANSITION_REFUSED`.
- `_Snapshot.run_status` and `_Snapshot.task_states` in `meristem/graph/checkpoint.py` (`MERISTEM-HARNESS-CYCLE-R084.4`, `MERISTEM-HARNESS-CYCLE-R085.2`): the persisted fields. They replace `status`, `completed_tasks` and `inflight_tasks`.
- `meristem/ontology/run_state_machines.ttl`, new (`MERISTEM-HARNESS-CYCLE-R084.5`, `MERISTEM-HARNESS-CYCLE-R085.4`): the run and task machine classes, each a subclass of a core state-machine class.
