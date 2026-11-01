# MERISTEM-RUN quickstart

Run the served proof of the run loop and the guards from a checkout:

```
uv run pytest tests/test_run_loop.py tests/test_checkpoint.py tests/test_hosted_run.py -q
uv run pytest tests/gates -q
```

Scenario: a host plan with a received budget runs one task, publishes its events through the host ports and stops at the budget; `tests/test_run_loop.py` is the hot-path proof. The guard scenario adds a module outside the owner registry and expects `tests/gates/test_boundary_owner_guard.py` to fail.
