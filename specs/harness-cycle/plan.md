# MERISTEM-HARNESS-CYCLE — Implementation plan

Governing spec: [spec.md](spec.md). Rows: [requirements.md](requirements.md).

## Summary

The harness improves itself while a task runs. One module, `meristem/harness/harness_cycle.py`, owns the loop. A tick proposes candidate component versions, probes each under identical conditions, composes the adopted harness from the measured contrast, persists the result and reflects the lesson. Each step is one function with one refusal path.

Medulla owns the program optimization, ablation, evaluation-loop and parallel-engine substrate. It also owns the versioned components, the improvement library (MEDULLA-ORCH-VOCAB-R801, MEDULLA-ORCH-VOCAB-R802) and the contrastive composition decision (MEDULLA-DECISION-ENGINE-R803). Arbora owns the served operations (ARBORA-OPS-R068) and drives the tick. The harness package keeps the constraint engine, contract validator, edit engine, plan, plan runtime and quality gates. The consumer rows R003 onward file the in-run obligations of the medulla decision engine: run loop behavior, context assembly, fan-out, sandbox limits and trace emission. The harness decides nothing in them. Each threshold is a received value with a fallback default.

## Technical Context

- **Language/Version**: Python 3.14 (floor 3.12)
- **Primary Dependencies**: scion-sdk, the model client library; no server, MCP or storage package
- **Storage**: N/A; the harness holds no store and reaches the graph through the host engine decision port
- **Testing**: pytest; the proof level each row names, with recorded engine answers for consumer rows
- **Target Platform**: in-process library called by the arbora host
- **Project Type**: harness library
- **Performance Goals**: N/A; the cycle runs after a task completes and adds no latency to the run
- **Constraints**: the harness applies received decisions and holds no ranking; every refusal keeps the current harness
- **Scale/Scope**: R001 with 15 children plus the consumer rows R003 to R083

## Constitution Check

- R-I, the harness applies decisions and never makes them: pass. The engine owns the composition (MEDULLA-DECISION-ENGINE-R803). The cycle owns probe scheduling and persistence only.
- R-II, dependencies stay minimal: pass. The cycle adds no import of a server, MCP or storage package.
- R-III, a boundary owner exists for every directory: pass. `harness_cycle.py` sits under the registered `meristem/harness/` owner.
- Organization limits: pass. See the Build contract.

## Project Structure

```text
meristem/harness/harness_cycle.py  existing: HarnessCandidate, CycleOutcome, propose, probe_candidates, compose, persist, reflect, run_cycle_tick, drive_cycle_tick
meristem/host_ports.py              existing: HarnessCycleProvider, register_harness_cycle_provider, harness_cycle_provider
meristem/graph/runner.py            existing: execute_graph, the probe entry
meristem/harness/edit_engine.py     existing: apply_edits, the in-run harness switch
meristem/rlm/runner.py              existing: run_rlm, the consumer rows R017 to R037 attach here
tests/harness/test_harness_cycle.py existing: unit tests of the steps
tests/harness/test_harness_cycle_served.py  planned: the served journey of R001.9
tests/support/recorded_engine.py    planned: RecordedEnginePort of R077
```

## Phase 0: Research

No unknowns. The measurement contract and the host provider are open prerequisites owned by other repositories; the cycle refuses until they exist (see Phase 1).

## Phase 1: Design

### Existing system and reuse

The run loop enters through `execute_graph` at `meristem/graph/runner.py:96`. The host ports live in `meristem/host_ports.py`. `RunDecisionProvider` answers the run plan, `RunEventSink` receives run events and `ScaffoldPublisher` (`host_ports.py:552`) publishes plan revisions. The cycle reuses these ports and adds `HarnessCycleProvider` (`host_ports.py:218`) beside them.

### Architecture

The loop is one tick: propose, probe, compose, persist, reflect. The module holds no store and no ranking. It queries the library (MEDULLA-ORCH-VOCAB-R802.3), enters the probes through `execute_graph`, delegates the composition to the engine (MEDULLA-DECISION-ENGINE-R803) and writes through the generated-client envelopes. Arbora drives the tick through the `HarnessCycleProvider` port. Without a registered provider the port fails closed, as every other port does.

### Interfaces and data model

`HarnessCandidate` (`harness_cycle.py:62`) and `CycleOutcome` (`harness_cycle.py:80`) are frozen dataclasses that carry references, not content. The probe runs under an identical task, seed, model and budget, and the conditions digest (`conditions_digest`, `harness_cycle.py:162`) binds them. The compose call is one served `Decide` under the `HARNESS_CONTRAST` question kind. The persist step writes three graph facts: the adopted component version (MEDULLA-ORCH-VOCAB-R801.2), the contrast and the lesson (MEDULLA-ORCH-VOCAB-R802.1). The in-run harness switch reuses `apply_edits` at `meristem/harness/edit_engine.py:457`.

### Live integration path

Arbora registers `HarnessCycleProvider` at startup and drives one tick per task. The tick reads the candidate budget k from the stage allocation in the run decision (MEDULLA-DECISION-ENGINE-R136.3). The reflect step reports the outcome through the existing `RunEventSink`, and arbora writes the trace into medulla. Today no host registers a provider, so `drive_cycle_tick` (`harness_cycle.py:610`) has no production caller. `MERISTEM-HARNESS-CYCLE-R001.10` owns persistence through the engine decision port. `MERISTEM-HARNESS-CYCLE-R001.11` owns the host-registered tick; its registration code belongs to arbora. That arbora row waits for the release of MEDULLA-ORCH-VOCAB-R801.2, MEDULLA-ORCH-VOCAB-R802 and MEDULLA-DECISION-ENGINE-R803. The unit tests of R001.5 and R001.8 run against fake persist and decide ports. They prove the step logic and the closed port, not host registration or engine envelopes.

### Quality and release gates

Focused tests: `uv run --frozen python -m pytest tests/harness/test_harness_cycle.py -q`. Served proof: `uv run --frozen python -m pytest tests/harness/test_harness_cycle_served.py -q` (planned, R001.9). The language-native gates run through `.config/pre-commit.yaml`; the limits are in the Build contract.

### Risks, alternatives, and decisions

The cycle can drift from the engine on the contrast semantics. Decision: the engine owns the composition (MEDULLA-DECISION-ENGINE-R803), and the cycle owns only probe scheduling and persistence. The rejected alternative kept a local ranking inside the cycle. It fails the no-duplicate-authority check, because the measured contrast is the only authority.

### Probe-condition evidence gap

The R001.3 test proves that probe records carry the same digest of requested values. It does not prove that each host-admitted run uses those values. In `test_probe_shares_conditions_and_records_trace`, the requested model is `model-1`, while the host decision returns a `FunctionModel`. The test does not compare the admitted budget or model settings with the digest inputs. `execute_graph` passes the requested model and seed metadata to the host, but `resolve_plan` accepts the returned `RunPlan` without binding it to the probe's budget reference or verifying a seed. The recorded `VERIFIED` state is unit evidence for digest propagation only.

Before a host provider is enabled, each probe must bind to an owner-admitted plan. The probe must verify the actual model identity, supported seed settings, complete enforced budget and candidate version before model or tool effects. A digest over requested inputs cannot establish those facts. Row R001.13 refuses an unregistered host executor and a response for another run. It does not prove materialization. The open evidence is an effect-counting regression for a substituted model, seed or budget, and an installed hosted journey that reads the real admitted conditions and traces. Until both exist, the cycle abstains. This repository registers no provider.

### Measurement refusal

The current probe traces hold usage counters and completion status. They hold no owner-authenticated retry count, tool-selection quality, evaluation score or cost. The cycle does not convert those proxies into MEDULLA-ORCH-VOCAB-R802 Q32 measurements and does not treat a missing baseline as a measured run. Until the owner supplies a verified measurement contract, `measure_contrasts` and the cycle refuse with `measurement_unavailable` before they call Decide or persist an adoption. Tests that inject synthetic contrasts exercise downstream step mechanics only. They are not served proof.

R001.4 is not verified, because the code refuses before a real Decide call. R001.5 is not verified, because `persist` refuses without owner-issued evidence. R001.7 is not verified, because engine abstention cannot be reached without injected test data. The independent reflect test covers local event mechanics only.

The extension point is an engine-owned, verifiable measurement and adoption receipt. It binds the graph, actor, baseline, candidate, actual conditions and evidence. Once it exists, compose and persist accept and verify it before the corresponding effect. No caller-constructed object or callback is accepted as proof. `test_compose_delegates_to_engine`, `test_compose_abstains_when_engine_abstains` and `test_refusals_keep_harness` inject synthetic contrasts and remain downstream unit checks, not proof of a real engine Decide call.

### Consumer rows for medulla decisions

Rows R003 to R053 file the in-run harness obligations of the medulla decision engine. Each rollup row names one medulla row. Each child row names the meristem file, the planned test and the received decision. Rows R054 onward add received-value consumers of later engine decisions. [contracts/medulla.md](contracts/medulla.md) lists the pairs.

## Phase 2: Task approach

`tasks.md` holds one task line per row, producer first. The cycle steps come first (R001.1 to R001.8), then the engine port and host registration (R001.10, R001.11, R077, R079), then the served journey (R001.9), then the consumer rows by run-loop stage.

## Complexity Tracking

None. The constitution check passed.

## Build contract

The repository pre-commit hooks apply: `ruff-format`, `ruff-check`, `secrets`, `spec`, and the `boundary-owners`, `boundary-coverage` and `boundary-file-owners` hooks. The organization limits apply to the changed code: cyclomatic complexity 10 and cognitive complexity 15 per function, 35 statements, 20 calls, 3 positional arguments and 1 boolean parameter per function, 300 lines and 10 functions per file, and clone similarity 0.85 (dupehound). Each row names its proving test. No suppression, no baseline and no duplicate implementation.

## Production caller chain

1. `MERISTEM-HARNESS-CYCLE-R001`: arbora host (ARBORA-HOST-R035) → `meristem/host_ports.py::harness_cycle_provider` (`host_ports.py:304`) → `meristem/harness/harness_cycle.py::drive_cycle_tick` (`harness_cycle.py:610`) → `run_cycle_tick` (`harness_cycle.py:504`) → `propose`, `probe_candidates`, `compose`, `persist`, `reflect`. The probes run through `meristem/graph/runner.py::execute_graph` (`runner.py:96`).
2. `MERISTEM-HARNESS-CYCLE-R001.11`: the arbora host binding `arbora/agent_runs.py::bind_run_ports` (arbora repository, `agent_runs.py:613`) is planned to call `register_harness_cycle_provider` (`host_ports.py:288`). Today it registers no cycle provider, so no run drives a tick.
3. `MERISTEM-HARNESS-CYCLE-R003` to `MERISTEM-HARNESS-CYCLE-R083`: each consumer row names its own chain from `meristem.graph.execute_graph` or `meristem/rlm/runner.py::run_rlm` to the stage function that applies the received decision.
4. `MERISTEM-HARNESS-CYCLE-R084` to `MERISTEM-HARNESS-CYCLE-R087`: the in-run state machines. `meristem/graph/state.py::transition` owns the run edges and `meristem/graph/dependency.py` owns the fan-out task edges. Every status write in `executor.py`, `dependency.py`, `checkpoint.py` and `rlm/runner.py` calls them. The persisted run record belongs to medulla; the harness keeps no store.

## Cross-repo seams

- `MERISTEM-HARNESS-CYCLE-R001`: [contracts/medulla.md](contracts/medulla.md)
- `MERISTEM-HARNESS-CYCLE-R001.11`: [contracts/arbora.md](contracts/arbora.md)
