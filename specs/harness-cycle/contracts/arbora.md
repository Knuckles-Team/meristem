# Arbora seam contract

Provider: arbora ARBORA-HOST-R035
Consumer: meristem MERISTEM-HARNESS-CYCLE-R001.11

Arbora drives the evolution tick. It registers the cycle and probe providers at startup, runs each probe through the run executor and binds the engine decision port. Meristem runs the steps and reports through the run event sink.

## Decisions

| Capability | Owner | Decision |
|---|---|---|
| Host registration of the cycle and probe providers | arbora | Arbora calls `register_harness_cycle_provider` and `register_harness_probe_provider` (ARBORA-HOST-R035). Meristem adds no registration code. |
| Engine decision port | arbora | Arbora binds the port and its `log` call (ARBORA-OPS-R890). Meristem calls it and holds no client of its own. |
| Probe admission and execution | arbora | The host executor admits each probe run and returns its trace. Meristem checks the run identity of the reply. |
| Tick trigger | arbora | One tick per completed run. A probe run carries `metadata.harness_probe` and never starts a tick. |

## Allowed call direction

Arbora calls meristem through `meristem/host_ports.py`. Meristem never imports arbora.

## Wire shape

Arbora registers a provider callable through `register_harness_cycle_provider` (`meristem/host_ports.py:288`). `drive_cycle_tick` (`meristem/harness/harness_cycle.py:610`) calls it and raises `RuntimeError` when none is bound. The probe provider receives the requested component version, seed, model, budget and run identity and returns a `ProbeTrace` (`harness_cycle.py:145`). The outcome returns as one `harness_cycle_outcome` run event.

## Refusal codes

- `RuntimeError` from `drive_cycle_tick`: no cycle provider is registered.
- `measurement_unavailable`: the engine decision port is absent.
- A probe reply for another run is refused inside `probe_candidates`.

## Authoritative types

- Meristem: `meristem/host_ports.py` (`HarnessCycleProvider`, `harness_cycle_provider`, `harness_probe_provider`).
- Arbora: `arbora/agent_runs.py::bind_run_ports` (`agent_runs.py:613`) is the binding site; it registers no cycle provider today. `arbora/agent_host/engine_decisions.py` (planned, ARBORA-OPS-R890) will hold the decision port.

## Test vectors

- `tests/harness/test_harness_cycle.py` runs the closed-port and run-identity vectors.
- `tests/harness/test_harness_cycle_served.py` (planned, R001.9 and R001.11) drives a served tick with both providers registered.

## Implementing specs

| Side | Spec IDs |
|---|---|
| Provider | ARBORA-HOST-R035, ARBORA-OPS-R890 |
| Consumer | MERISTEM-HARNESS-CYCLE-R001.10, MERISTEM-HARNESS-CYCLE-R001.11 |
