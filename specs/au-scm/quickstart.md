# MERISTEM-SCM quickstart

Run the proof for `MERISTEM-SCM-R011.5` from the repository root with the project environment.

1. `uv run pytest tests/harness/test_changeset_loop.py` proves the changeset cycle that the served run reuses.
2. `uv run pytest tests/harness/test_task_run_served.py` (planned) starts a task run through the host run start operation against a loopback engine and observes the landed changeset and its evidence, then a failing gate and its typed failure.
3. `uv run pytest tests/harness/test_delivery_run_served.py` (planned) proves `MERISTEM-SCM-R012.8`. It starts a delivery assignment against a loopback engine. It delivers a CI failure and a review finding. It observes one revision, one resolved thread and a `ready` report.
