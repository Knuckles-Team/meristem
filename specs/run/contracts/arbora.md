# Arbora seam contract

Provider: arbora ARBORA-HOST-R007
Consumer: meristem MERISTEM-RUN-R001

## Decisions

| Capability | Owner | Decision |
|---|---|---|
| Public REST, MCP and A2A serving, gateway, multiplexer | arbora | Served only by arbora. Meristem hosts no server, A2A or ACP endpoint. |
| Deployment, installation, environment diagnostics, skill certification and the certification identity authority | arbora | Owned by arbora. Meristem has no `deployment/` package. |
| Fleet reconciliation, autoscaling, scaling authority and deploy watch | arbora | Owned by arbora. Meristem has no orchestration package. |
| Messaging adapters, request identity, readiness | arbora | Owned by arbora. |
| Process configuration | split | Connector base settings belong to the connector SDK. Meristem keeps only the harness settings in `core/config.py`, read through one environment reader. |
| Run execution | meristem | Meristem runs the loop. Arbora admits the run and passes the plan through host ports. |

## Allowed call direction

Arbora calls meristem only through `meristem.api` and the public host module set. Meristem never imports arbora; it receives each decision and checkpoint through the host ports bound for the run.

## Wire shape

No served record crosses this seam. Arbora calls meristem in process and binds the host ports below; meristem never imports arbora.

| Direction | Call | Where |
|---|---|---|
| arbora to meristem | `AgentRunExecutor.execute_agent`, `iterate_run`, `iterate_client_run`, `run_rlm`, `execute_task_plan` entry points | `meristem/graph/executor.py`, `meristem/harness/hosted_run.py`, `meristem/rlm/runner.py`; called from `arbora/agent_runs.py`, `arbora/api/ops/plans.py`, `arbora/acp/adapter.py`, `arbora/api/contracts/agent_control_plane.py`, `arbora/a2a/anonymous_send.py` |
| arbora binds | `register_run_decision_provider`, `register_model_provider`, `register_run_event_sink`, `register_optional_scaffold_publication_factory`, `register_run_history_provider`, `register_tool_caller`, `register_run_checkpoint_factory`, `register_active_child_input_factory`, `register_durable_run_event_factory` | `meristem/host_ports.py`; arbora binds the first four in `bind_run_ports` (`arbora/agent_runs.py`) and the history reader in `arbora/acp/local_runtime.py` |

The run plan is the one value that crosses: the decision provider returns a `RunPlan` (`model`, `steps`, `tool_names`, `toolsets`, `budget`, `stop`, `harness`, `client_transport`, `dependency_plan`, `initial_usage`, `render_receipt`), and `iterate_run` yields event dicts whose last event carries a `RunResult`.

## Refusal codes

Meristem refuses with typed exceptions and never substitutes a local default for an unbound or invalid host reply:

- `RunPlanUnavailable`: no model in the plan.
- `TypeError("the run decision provider must return a RunPlan")`: the provider returned another type.
- `ToolCallerUnavailable`: a tool step with no bound `ToolCaller`; raised before any task starts.
- `CheckpointUnavailable`: the bound checkpoint port is unusable.
- `RenderReceiptRefused` with `receipt_invalid`, `receipt_stale`, `receipt_substituted`, `receipt_wrong_scope`, `consumer_undefined` or `revisions_unavailable`.
- `ClientCapabilityRefused`: the bound client transport denied a call.
- `RuntimeError("run checkpoint provider failed")`: a malformed or foreign history selection.

## Authoritative types

- Meristem: `meristem/host_ports.py` (port protocols and `register_*` functions), `meristem/graph/state.py` (`RunPlan`, `RunRequest`, `RunResult`, `RunBudget`, `StopRule`, `HarnessConfig`), `meristem/graph/render_receipt.py`.
- Arbora: `arbora/agent_runs.py` (`AgentRunPort`, `bind_run_ports`, `decide_run`).

## Test vectors

- `tests/test_host_ports.py`, `tests/test_run_loop.py` and `tests/test_hosted_run.py` hold the meristem-side vectors with a recorded host.
- `tests/gates/test_boundary_removal_census.py` holds the absence checks for the capabilities arbora owns.
- The arbora side proves the binding in its own host-composition tests.

## Implementing specs

| Side | Spec IDs |
|---|---|
| Provider | ARBORA-HOST-R007, ARBORA-HOST-R011, ARBORA-HOST-R027.1 |
| Consumer | MERISTEM-RUN-R001, MERISTEM-RUN-R008, MERISTEM-RUN-R014, MERISTEM-RUN-R016 |
