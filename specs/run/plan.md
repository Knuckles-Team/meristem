# MERISTEM-RUN — Implementation plan

## Summary

Meristem runs one agent run. It applies the decisions and limits it receives through host ports and holds no store, planner, selector, router, retriever or policy. Ownership of every released capability is fixed by the contracts in `contracts/`; the guards in this spec keep the repository to its owned paths.

## Technical Context

Language/Version: Python 3.12 floor, 3.14 standard.
Primary Dependencies: pydantic, pytest.
Storage: none; usage, memory and checkpoints are held by the owners named in the contracts.
Testing: pytest with `@pytest.mark.spec` bindings; gate scripts under `scripts/check_boundary_*.py`.
Target Platform: library executed by the arbora host.
Project Type: single Python package.
Performance Goals: no per-step overhead beyond the received limits.
Constraints: no served surface; every threshold is a received value with a default.
Scale/Scope: one run per call, bounded by the received budget.

## Constitution Check

- Meristem applies received decisions and holds no store or policy: pass.
- No served surface or direct engine call: pass.
- Specs state current requirements only: pass.

## Project Structure

- `meristem/graph/`, `meristem/rlm/`, `meristem/harness/`, `meristem/runtime/`, `meristem/core/`, `meristem/skills/`
- `scripts/check_boundary_owners.py`, `scripts/check_boundary_coverage.py`, `scripts/check_boundary_file_owners.py`
- `tests/gates/`, `tests/`

### Current package-directory ownership

Meristem retains the first two directory levels of its current package. A directory move changes this inventory and its owner decision in the same change. The owner guard checks Python modules on its own.

| Directory | Disposition | Destination |
|---|---|---|
| `meristem/core` | keep | meristem |
| `meristem/graph` | keep | meristem |
| `meristem/harness` | keep | meristem |
| `meristem/rlm` | keep | meristem |
| `meristem/runtime` | keep | meristem |
| `meristem/skills` | keep | meristem |
| `meristem/skills/meristem-development` | keep | meristem |

## Phase 0: Research

No unknowns.

## Phase 1: Design

The run-loop design is in the amendment sections below. The seams to other repositories are the contracts in `contracts/`.

### Amendment 2026-10-18: retain the run scaffold after the boundary cut

### Provenance and migration

The deletion commit [c45f277c](https://github.com/Knuckles-Team/agent-utilities/commit/c45f277c061c12e654b80fa12d1cf8f11f01d5ac)
names the public pre-cut snapshot
[5eb8d85a](https://github.com/Knuckles-Team/agent-utilities/tree/5eb8d85a3c5d0c5d07babd55e983ecd9778d6d81).
Its [R014 requirements](https://github.com/Knuckles-Team/agent-utilities/blob/5eb8d85a3c5d0c5d07babd55e983ecd9778d6d81/specs/harness-evolution/requirements.md)
and [current-plan design](https://github.com/Knuckles-Team/agent-utilities/blob/5eb8d85a3c5d0c5d07babd55e983ecd9778d6d81/specs/harness-evolution/plan.md#public-current-plan-snapshots-au-harness-r0146-to-r01411)
are the source of this migration. That design explicitly recorded the absence of
`layers/harness_plan.py` and the producer runtime. A historical requirement is
not evidence of a shipped feature. Closed, unmerged
[AU PR #302](https://github.com/Knuckles-Team/agent-utilities/pull/302) supplies no landing evidence.

| Historical requirement | Current owner and acceptance |
|---|---|
| harness-evolution R014.1 | MERISTEM-RUN-R001.4.1: whole-list mutable run scaffold with frozen items |
| harness-evolution R014.2 | MERISTEM-RUN-R001.4.2: configuration-gated validated update tool |
| harness-evolution R014.3 | MERISTEM-RUN-R001.4.3: per-request injection outside stored history |
| harness-evolution R014.4 | MERISTEM-RUN-R001.4.4: no planner or selection authority |
| harness-evolution R014.5 | MERISTEM-RUN-R001.4.5: two-update end-to-end proof |
| harness-evolution R014.6 | SDK-owned request/snapshot models; do not restore AU API DTOs |
| harness-evolution R014.7 | AU emits the real scaffold revision and lifecycle; SDK defines the snapshot and EG owns any plan record |
| harness-evolution R014.8–R014.10 | Arbora admission, caller checks and read composition; AU supplies actual run state through host ports |
| harness-evolution R014.11 | Cross-owner served proof, ending at arbora-ops R021.4.5 and the inspector |

The [current ecosystem boundary standard](https://github.com/Knuckles-Team/pipelines/blob/main/reference/ecosystem-boundaries.md)
explicitly routes current-plan reads: the plan record belongs to EG, snapshot
models to SDK, and authorized reads to Arbora. This supersedes the historical
`AgentControlPlane.current_plan` placement, not its acceptance. No deleted AU
server, public API, lookup store or planner is restored.

### Existing implementation and next slices

At AU `6a18dd2`, `graph/executor.py::iterate_run` executes received
`RunPlan.steps` and emits transitions through `host_ports.emit_run_event`.
`graph/state.py::HarnessConfig` has retries, response format and model settings;
it has no scaffold decision. No `PlanState`, update tool or injection hook exists.
Execution prompts are not the model-edited scaffold. Those transitions cannot
truthfully supply `current_plan`.

Implement R001.4.1 first under `harness/`, then wire R001.4.2 and R001.4.3 into
the existing loop under a received scaffold decision. Extend the real typed
configuration and host decision integration with tests; do not infer a decision
from metadata or invent configuration fields at a consumer. R001.4.4 and
R001.4.5 prove this wiring. The original absolute ban on plan text in stored
history also applies to update-tool arguments and observations; returning an
observation is not permission to persist it.

### Preserved snapshot acceptance and implementation dependencies

The following obligations remain required, not implemented by this amendment:

- Initialization starts at revision zero. Each accepted whole-list replacement
  increments once, including identical content. Failed validation changes neither
  items nor revision. Reads never increment revision. Capture items and revision
  atomically under the update synchronization.
- Observation and expiry are UTC. `snapshot_validity_seconds` has the historical
  replaceable default of 30; composition validates a positive finite bounded
  value. Expiry is observation plus validity, capped by the active run lease.
  An expired lease is unavailable. The historical text gives no numeric upper
  bound; its configuration owner must define that bound before runtime acceptance.
- Bind the real scaffold to the authenticated execution tenant, principal and
  run. Arbora checks authority on every read; request parameters carry only run
  selection. Unknown and foreign runs have the same unavailable result.
- SDK request fields contain only `run_id`; snapshot fields are `run_id`,
  `revision`, `observed_at`, `expires_at` and ordered `items`.
  SDK models remain strict and frozen, reject unknown fields, and require a
  nonnegative integer revision. The read has `availability` and nullable
  `snapshot`: `available` requires a snapshot and `unavailable` requires null.
  Snapshot expiry must be strictly later than observation.
- Preserve ordered items and the three statuses. Available empty state differs
  from unavailable state. Malformed, wrong-run, future or expired snapshots fail
  closed. Regressing revisions fail closed within the same run.
- Disabled scaffolds, ended runs, lost state and absent ports are unavailable.
  Termination releases state; a restarted runtime uses a new run ID. No model
  call or history reconstruction serves a read. Diagnostics contain outcome,
  run ID, revision and timing only, never plan text or credentials.
- SDK/EG owner contracts and their published interfaces must precede production
  snapshot composition. AU emits actual state through `host_ports`; it adds no
  durable record or global read registry. Arbora retains admission and the
  read-only operation. No public plan mutation is introduced.

Consumer dependency:
[arbora-ops R021.4.5](https://github.com/Knuckles-Team/arbora/blob/2d62cd8/specs/hosted-api-operations/requirements.md)
remains unavailable until the scaffold children, the SDK/EG contract assignment
and producer bridge, and Arbora composition have independent evidence. Its
reference to harness-evolution R014 now resolves through the migration table above.
Two updates, expiry, denied refresh and run-switch clearing still require
served acceptance. This amendment claims neither `runs.get` completion nor
inspector availability. At migration time all new implementation tasks remained unchecked; the
implementation section below records subsequent scaffold delivery.

### Scaffold loop implementation

`HarnessConfig.plan_scaffold` is the received `on`/`off` decision, defaulting to
`off`. `iterate_run` creates one `harness.plan.PlanState` when enabled and passes
it to `build_agent`. `harness.plan_runtime.PlanScaffold` registers the sequential
`update_plan` tool and shares that same state with `before_model_request`.
Malformed items produce an `invalid_plan` observation without changing state.
The hook adds request-only context data; it does not write injected text to
history. It replaces stored scaffold tool arguments and observations with
payload-free parts while preserving tool-call identifiers. Cleanup also runs on
budget/error/cancellation exit, using the history captured at the model boundary.
Each agent call gets separate cleanup tracking while successive steps of the
same outer run share the scaffold. Existing configuration positional arguments
remain unchanged.

A host-supplied `RunPlan` can enable this with
`HarnessConfig(plan_scaffold="on")` through the existing run decision provider.
`RunPlan.steps` remain execution prompts. This config field does not add the
historical context-policy or action-space fields. No snapshot reader is exported
by this slice: revision, lease/scope binding, SDK/EG contracts and Arbora read
composition remain prerequisites to the unchanged `runs.current_plan` contract.

### Next producer slice: atomic revisions and explicit owner prerequisites

MERISTEM-RUN-R001.5.1 implements only the atomic revision mechanism from historical
harness-evolution R014.7. `PlanState.read_versioned()` returns `(revision, items)` from
the very state updated by `PlanScaffold.update_plan`; it introduces no wire DTO,
clock, authority claim or registry. Validation completes before the update lock;
revision increment and replacement share that lock with reads. Two writers may
finish validation in either order, but each accepted replacement gets one unique
revision and readers never mix revisions with another update's items.

The following exact interfaces must exist before the snapshot bridge can ship:

1. SDK owns strict frozen `CurrentPlanRequest`, `CurrentPlanSnapshot` and
   `CurrentPlanRead` semantics already listed above. Preserve Arbora's current
   `run_id`, nonnegative `revision`, UTC `observed_at`/`expires_at`, ordered text
   and status items, and available/snapshot versus unavailable/null invariants.
   This is a planned owner interface, not an existing SDK import. The available
   SDK source at `a0f78fe` contains no current-plan definition; refresh its main
   before assigning a row and reusing or adding its export.
2. EG owns any plan record and run-lease fence used to locate or retain published
   state. Extend its existing run/WorkItem authority, not an AU or Arbora lookup
   store. The inspected EG source at `8857234` contains no `CurrentPlan` contract.
   The smallest prerequisite must bind publication and readback to authenticated
   tenant, principal, run ID and the active run lease; reject stale fences,
   regressing revisions, ended runs and wrong owners. No engine method name is
   invented here. This requires an owner-native row and public client contract.
3. Arbora's `agent_runs.AgentRunPort.execute_agent` already receives a verified
   `GraphSession`; AU's `AgentRunExecutor.execute_agent` forwards that session in
   run metadata. `a2a.authority.WorkItemA2AAuthority._run` claims a WorkItem through
   `work_items.claim_specific`, but does not pass the claim to that execution
   port. The bridge must carry the real run-lease deadline and fence from this
   authority. A credential expiry or `continuity_expires_at` is not the run lease.
   Do not infer authority or lease expiry from request payloads or run-ID knowledge.
4. Arbora must recheck session authority and `kg:read` at each read, bind the
   requested run to the execution tenant/principal, and return the same unavailable
   shape for absent, ended, foreign or expired state. Compute UTC expiry as
   observation plus the validated positive bounded validity (default 30 seconds),
   capped by the active run lease. Reject future observations, expired intervals
   and revision regression. Its `api.ops.runs.CurrentPlanReader.read` already
   takes `run_id`, `tenant`, `principal`; `mcp_server.server` does not bind that
   reader. Preserve the operation's wire shape when composing the published
   SDK/EG interfaces.
5. Operational scaffold activation also needs the real host decision. At inspected
   Arbora `2d62cd8`, `agent_runs.decide_run` uses `default_run_plan` and reports no
   served run-plan decision method. It does not enable the scaffold. The existing
   EG owner is `medulla-decision-engine R128` (`harness_config`, including `.3` answer
   and `.6` scaffold rule); refresh its producer/client evidence before wiring
   the result. A test-supplied `HarnessConfig(plan_scaffold="on")` proves the AU
   mechanism, not an authenticated deployed decision or inspector journey.

These source observations are bounded to the stated local revisions. They do
not assert that newer owner branches lack a contract. No live expiry, caller
binding, reader availability or served acceptance is claimed by the revision
slice. The next cross-owner change must supply these prerequisites or keep the
read truthfully unavailable.

### Run-local publication mechanism

MERISTEM-RUN-R001.5.2 adds an optional host factory and explicitly injects its
run-local publisher into the scaffold capability. The host receives the run
request for binding but must obtain verified authority and a real lease itself;
metadata is not authority. AU awaits the initial atomic revision and accepted
replacements, without synthesizing refresh revisions. A retained publisher is
deactivated before asynchronous close. Publication failures stop the run without
rolling back a possibly already published replacement. No ContextVar binding
crosses async-generator yields. Off and unbound execution remain local only.

SDK current-plan contracts are now published in 0.2.2 and EG native ownership
landed in #663, but EG 2.27.5 installed acceptance remains pending. This slice
does not compose Arbora's reader, enable a host harness decision, or establish
lease-bound served availability. Those owner prerequisites remain separate.


### Amendment 2026-10-20: received attachment input bounds

MERISTEM-RUN-R001.1 also applies to admitted binary model inputs. The host
provides inline `pydantic_ai.BinaryContent` values in `RunRequest.attachments`
and explicitly supplies the selected model's permitted media types in
`RunPlan.input_media_types`. The default is empty and refuses attachments.
`RunBudget` supplies aggregate byte and part-count limits (fallbacks: 1 MiB
and 8 parts). AU validates these before constructing a model or emitting
`run_started`, injects the content on the first executed step only, and retains
it through the normal model history. UTF-8 text becomes user text; image bytes
remain binary input. No URL, filename read, media download, capability guess,
policy grant, storage or provider configuration change is introduced.

This is an enforcement prerequisite for arbora-a2a R001.5, not served attachment
acceptance. Arbora must obtain the selected model's authoritative input-media
decision and validate it before admitting a WorkItem. Its current registry has
no such capability field or producer. A chat-completions wire transport alone
is no evidence that a selected model supports images. Full acceptance still
requires a supported-model journey and zero-task rejection through Arbora.

### Amendment 2026-10-20: shared measured request timing

MERISTEM-RUN-R001.1 emits run-local timing measured by the harness's monotonic
clock, alongside each actual model request's response usage and attribution.
A request duration includes network and provider waiting and is never labeled
provider generation time or used to calculate generation TPS. The run duration
includes plan resolution, tools and retries; it is not the served HTTP latency.
Unknown provider generation duration, queue wait and TTFT remain absent. Keep
request samples bounded, expose incomplete coverage explicitly, and emit the
same result through the host event sink so Arbora, WebUI, TUI and Geniusbot can
consume one producer. EG retains durable aggregation; AU adds no store or price
calculation. Real model execution tests cover repeated calls, failures and
malformed usage without timing sleeps or fabricated generation measurements.

MERISTEM-RUN-R001.5.2: The optional publication registration accepts a per-run `None` binding; AU keeps the local scaffold usable and closes the host scope normally. Existing required-publisher registration remains compatible.

### Amendment 2026-10-22: received dependency execution

The ecosystem boundary standard assigns task planning and bounds to EG R134.
Arbora owns admission and replay. AU R001.6 and R001.7 own in-run fan-out.
`graph/dependency.py` applies a received DAG. `graph/budget.py` reserves the
received aggregate limits in one event loop. Neither module chooses a plan.

`RunPlan.dependency_plan` is exclusive with serial `steps`. `RunTask` carries
a task ID, prompt and direct dependency IDs. `DependencyPlan` carries tasks,
an output task ID, a positive concurrency limit and optional plan identity.
The output task is the sole sink, so all admitted work contributes to it.
Malformed, cyclic or over-step-budget plans fail before a model is built.

Only dispatched tasks get an asyncio task. A ready queue holds the rest as
bounded plan data. Each completion can refill a slot; joins require all named
prerequisites. Task histories are isolated. Direct prerequisite outputs enter
as JSON data in declared order. Binary inputs stay with the first declared
task, not every child. The host keeps all model, tool and identity bindings.

Failure is fail-fast: cancel and await siblings, report undispatched tasks as
blocked, then emit the parent terminal event. A stop marker stops the whole
plan. No task-level retry or implicit synthesis is added. Existing model/tool
validation retries share the same request and tool reservations. Tokens use
reported response accounting; the harness cannot promise zero in-flight token
overshoot. Terminal output comes only from the designated output task.

All producer events receive a one-based per-run sequence and UUIDv5 event ID
over the run ID and sequence. Task events report execution state and output,
never private model reasoning. Arbora owns safe replay projection. Existing
`graph_complete`, `error` and ordered-step events remain compatible.

Legacy recursive `sub_query` calls still wait for the parent model to choose
its next tool batch. This differs from a declared DAG.
AU does not invent a dependency or a future child to bypass that choice.

Recursive calls share the same reservation mechanism. Their received
`max_concurrent_requests` caps model calls, not waiting parents. This prevents
a nested child from waiting for a permit that its parent holds. The fallback
is four requests and fifty tool calls. A tool response batch above the received
tool limit fails before framework fan-out. The host request limit of zero is
preserved, not replaced with a fallback. Tokens include reported fallback
attempts. Host tools own cancellation of external operations that outlive
their coroutine. AU does not claim to stop an uncooperative external process.

A single driver runs the DAG independently of event consumption. Its queue
holds at most two lifecycle events per declared task plus an end marker. A
slow viewer cannot hold a newly ready task behind an unrelated task. Model
output and dependency output are data; they cannot change the admitted DAG,
its IDs or its limits. The host must supply a fresh run ID for each execution.
Stable event IDs support repeat delivery of the same event, not crash recovery
or durable active-run resume. AU has no event store or restart coordinator.

Parallel tools use a finite fallback of fifty calls when the host omits that limit.
Explicit finite limits remain unchanged. Root-only recursive depth zero remains valid.
Model handlers use one tracked task per request permit. Cancellation drains that
handler before accounting usage or releasing its permit. This protects awaited
cleanup from the model framework's repeated cancellation scopes.

Setup and the DAG driver share one absolute deadline. A paused reader cannot
prevent child timeout and cleanup. Consumers must finish or close the iterator.
A pause can delay the parent terminal event and host-scope release after work stops.

The drain guarantee covers AU-owned tasks and model request handlers.
Host extensions own cancellation-safe asynchronous scope exits. Repeated direct
cancellation can interrupt a host exit after AU finishes child cleanup.
AU does not move host exits to another task, which may break ContextVar ownership.
No elapsed cleanup bound applies to handlers that do not cooperate.

### Amendment 2026-10-22: account for prior host consumption

MERISTEM-RUN-R001.7 applies the original host bounds to prior measured work
and in-run execution. `RunPlan.initial_usage` uses the existing pydantic-ai
`RunUsage` type. `initial_elapsed_seconds` carries measured time before AU
execution. Both default to zero. They are trusted host inputs, not model
proposal fields. Request metadata and the model scaffold cannot set them.
Arbora supplies its real planner usage and elapsed time. EG still owns task
choice and admission bounds; AU adds no scheduler, planner or store.

AU snapshots the public request, tool, input-token and output-token counters
before yielding or binding a model. Counts must be nonnegative integers, not
booleans or fractions. Total tokens are the sum of input and output tokens.
Elapsed time must be finite and nonnegative. The public usage shape stays
unchanged. Extended provider metrics remain outside this accounting change.

The host keeps the original `RunBudget`, not a pre-subtracted remainder. AU
seeds one aggregate ledger and subtracts initial elapsed time once from the
existing setup/driver timeout. An exhausted initial request, token or time
bound stops before model binding and child dispatch. Prior tool usage above
its limit also stops. An exact tool limit allows model-only completion, but
AU refuses additional tools before their execution. Terminal usage
includes validated initial counters on completion, refusal, failure and
cancellation. Reusing a plan does not mutate or reuse its run ledger.

Nonzero initial consumption also opts serial steps into the existing shared
budget. They cannot reset the initial charge for each step. Zero-default
serial behavior remains unchanged. All rolling and cancellation guarantees
and residuals above still apply. The harness does not estimate missing token
usage or know in-flight token counts before the provider reports them. The
host must not add planner counters to AU results a second time.

### Failed plan resolution after measured host work

The trusted decision provider can raise `RunPlanFailure` after measured work.
Later admission, validation or publication may fail before a `RunPlan` returns.
The carrier requires the original `BaseException` and
reported `RunUsage`. It accepts the same measured `initial_elapsed_seconds`.
AU validates and snapshots these values with the existing initial-usage
helper. Invalid input fails closed without a partial ledger.

The carrier grants no execution, supplies no budget and cannot widen one.
AU includes its counters once in the failed terminal event and result. It
does not clamp consumed usage to a limit or replace the original failure
with a budget refusal. Cancellation and iterator close emit the measured
ledger in a stopped cancellation event and re-raise the original exception.
A raw provider error or cancellation without a reported ledger keeps its
previous behavior. AU adds no estimate or second accounting path. Model
proposals and request metadata cannot supply a trusted failure carrier.

### Failed executor receipts

`run()` still returns its failed `RunResult`. The execution port raises
`RunFailed(result)` instead of discarding that result in a plain `RuntimeError`.
The subclass keeps the same message and catch compatibility. Its read-only
`result`, `run_id` and `usage` properties expose one aggregate receipt.
The allowed host boundary `meristem.runtime` lazily exports the same
`AgentRunExecutor`, `DependencyPlan`, `RunBudget`, `RunFailed`, `RunPlan`,
`RunPlanFailure`, `RunTask`, `StopRule` and `default_run_plan` objects.
This facade adds no implementation or policy and does not load the model
runtime merely to import workspace capabilities.

The constructor copies and validates a failed result before retaining it.
Scalar fields keep their declared types; steps are nonnegative integers.
Run IDs must be nonempty strings, not necessarily UUIDs. Empty usage stays
empty. Other ledgers contain exactly the five public counters as nonnegative
integers, excluding booleans. Total tokens must equal input plus output.
The retained result and its usage mapping are immutable. `result.as_dict()`
returns plain data. Exception copy and pickle round trips retain the receipt.

The executor constructs this exception only from its own returned result.
It never trusts receipt attributes on a provider or model exception. Hosts
must recognize the typed failure and verify its expected run identity before
copying counters. They keep the outcome failed and must not publish a partial
answer. This receipt supplies no execution, retry or budget grant. The host
must not add it to the same terminal ledger again. Successful and stopped
returns, cancellation and the existing event schema remain unchanged.

### Client capability tools — 2026-10-20

R001.11 restores the in-run part of historical meristem-control R044 through the
current RunPlan seam. The host supplies a bound ClientCapabilityTransport,
a closed tool selection and limits. The executor binds only that selection.
The harness forwards requests and bounds request size, response size and wait
time. It never opens files, starts processes, chooses grants or stores approvals.
Arbora owns session binding and dispatch; the connected client owns containment.
Permission-required and denied responses remain refusals. A timeout does not
assert rollback or retry a possibly dispatched effect. Trace records contain
method, byte counts and outcome only. Mutations remain unavailable unless the
host can obtain the existing atomic approval authority.

### Amendment 2026-10-22: render-receipt consumer moved from pipelines

`MERISTEM-RUN-R002` was `pipe-pages R036` in pipelines. Checking a render receipt before model transport is in-run harness work, so the run loop owns it. The engine produces the receipt under medulla-typed-packs R002.10 and names this row as its dispatch consumer. The rows were SPECIFIED with no bindings, so no evidence moved.
### Amendment 2026-10-22: explicit measurement producer ownership

MERISTEM-RUN-R001.12 owns the emission implemented in agent-utilities #328. It replaces the overly broad R001.1 binding on the metric tests, not the received-plan requirement itself. medulla-orch-vocab R011 is a downstream vocabulary consumer, not the producer owner. Durable aggregation remains in EG; served composition remains in Arbora. Provider timing and live/release acceptance remain unproved.

### Completed text-history hydration

R001.9.1 adds `harness/conversation_history.py` for the harness-owned message
codec. The host provider returns JSON with `version: 1`, `phase: "completed"`,
`run_id` and `messages`. The source run must differ from the current run. The
envelope is bounded to one MiB and 1024 messages. Each message has exactly
`role` and `text` fields. Fresh model-message objects are constructed from
those fields; provider state, instructions, usage and metadata are refused.
It accepts alternating text
user requests and assistant responses only; tool and system parts refuse.
Arbora owns authorization and must provide only a completed prior context turn.
AU validates the content, not the host authority. It stores nothing.

The sequential executor loads history before model construction. Missing host
providers return no history. Provider failure refuses without private details.
A dependency plan with history refuses before any effect until equivalent
semantics exist. R001.8 checkpoint restore and R001.9 native and served
acceptance remain open. This increment cannot resume an interrupted run.

R001.9.2 retains the parent's remaining checkpoint integration and current-run
precedence acceptance. Its native and served checks remain pending, so the
R001.9 rollup must not become verified from text-only R001.9.1 coverage.


### Single-read history selection and remaining restore contract

R001.9.2.1 extends the existing host port, not a client transcript store.
The host supplies a frozen RunCheckpointSelection for one requested run.
It must authorize caller and conversation and read one consistent native view.
Both presence fields are mandatory. None means absent, not an unavailable read.
A bound provider failure or malformed result never falls back to legacy history.
A current-run checkpoint always blocks unsupported restoration before planning.
The harness never treats its content as prior-turn history or starts over.
Without a current checkpoint, the existing strict completed-text codec hydrates
a fresh turn. Old plans, outputs and usage never enter that codec.
The old history provider remains supported only when the new port is unbound.

The consumer cannot establish atomicity across two independent EG reads.
Arbora must bind a native consistent selection or a verified revision-fenced
conversation view. Until that producer exists, it must not report a complete
selection by stitching together unchecked current and prior snapshots.
These harness tests prove call order and no fallback, not native atomicity.

R001.8 and R001.9.2.2 require the following genuine restore contract:

- Bind the checkpoint to the original admitted run and immutable received plan.
  Include instructions, model and tool grants, attachments and plan revision
  in the host-validated execution binding. Refuse mismatches before effects.
- Publish an in-flight marker before each model or tool effect. Await durable
  acknowledgement before executing. Publish completion before exposing output.
  Native CAS, fencing and retry identity govern publication; AU stores nothing.
- Restore only a validated completed step. An uncertain in-flight effect refuses
  rather than replays. A lost acknowledgement requires native receipt recovery,
  not a second effect. Tool transaction reconciliation remains host-owned.
- Version the supported message representation. Restore actual model messages
  and completed outputs without importing provider-private execution authority.
  Refuse unsupported tool or multimodal history rather than silently dropping it.
- Restore current-run cumulative usage and elapsed consumption into SharedBudget.
  Account for new planning work exactly once and never reset a resumed budget.
  Prior-conversation history must still start fresh budgets and outputs.
- Preserve dependency task identities, completed outputs and causal dependencies.
  Resume the existing bounded executor with one shared budget. Refuse any
  uncertain task until equivalent no-replayed-effect semantics are implemented.
- Qualify restart with the native producer and admission-bound Arbora adapter.
  Prove owner/context isolation, same-run precedence, expired-fence refusal and
  exact no-repeat effects. Client browsing is not successful continuation.

This increment deliberately refuses current-run restore. It does not complete
checkpoint publication, dependency restoration or served conversation resume.

### Received text-only execution

R001.13 adds a host-received constraint, not a policy decision. The initial supported protocol is exact OpenAIChatModel with OpenAIProvider and AsyncOpenAI. The SDK must have zero automatic retries. Provider defaults and per-request settings accept only a bounded max_tokens setting. The host owns and closes the client. AU creates no credentials or deployment.

The constrained path uses one text step and the existing shared usage accounting. It skips inherited history, lease and scaffold hooks. Model replies cannot grant tools. Provider-reported tokens remain post-response evidence; max_tokens bounds output only. Arbora still owes explicit anonymous admission, finite per-run and in-flight limits, native ownership and durable cancellation/outcome composition before no-auth Chat can be enabled. R001.13.2 keeps those installed obligations open.

The restricted run emits no inherited global event sink. Its public result retains measured usage on returned terminals. Cancellation propagates only after provider cleanup; it supplies no settlement receipt. The host must retain its full reserved quota for a cancelled or uncertain attempt. The host owns the borrowed model client and closes it after execution and drain, not while requests remain active. The request fence uses the exact absolute deadline of the enclosing executor timeout.

A durable cumulative spend quota is not promised by this local text slice. If an operator configures such a quota, its owner must enforce it; process-local counters cannot satisfy a durable promise. The default acceptance requires bounded runs and in-flight capacity, not a new native spending subsystem.

### Typed tool steps in dependency plans (MERISTEM-RUN-R001.6.1 to R001.6.7)

A received `DependencyPlan` may hold tool steps beside prompt steps. Today the harness appends prerequisite outputs to the next prompt. The model copies values, so a value such as an incident number is not reliable. A tool step names a call and binds its arguments to prerequisite outputs by path. A value then moves by resolution, not by copying.

Call path: `execute_graph`, then `_execute_dependency_plan` (`meristem/graph/dependency.py:99`). For each ready task, a prompt step runs the model as before. A tool step runs the pure resolver, then the host-bound `ToolCaller`, then records the step.

The engine decides the call and the binding form (`medulla-decision-engine R134.111.8`). Arbora admits the run and supplies the governed caller (`arbora-release R003.4.7`). AU holds no tool client, no authority rule and no store. It resolves, calls the port and reports.

A missing path is known only at run time. It is a typed step failure and never a model retry. A failed step leaves its dependents unstarted under the drain rules of `MERISTEM-RUN-R001.7`.

### 2026-10-24: child execution identity, input and public records

The existing R001.6 dependency executor, R001.7 event ordering/draining,
R001.8 checkpoints, R001.9 history selection and R001.10 publisher own these
increments. Reuse `RunTask`, `DependencyPlan`, the runtime loop and host ports.
Do not add a server, event store, planner, authority table or a second runtime.
Arbora serves the graph and actions under `arbora-ops R021.4.8` / `.9` and
`arbora-release R003.4.12` / `.13` in its existing owner specs.

Current dependency children share their parent's `run_id`; their `task_id` is
local to that run. Recursive RLM depth and `_Env` objects provide no durable
child identity. R001.14 records only explicit admitted identity and spawning
references. The shared projection uses `(run_id, node_kind, local_id)` keys;
spawned-child containment and task dependency edges are distinct. A new nested
run needs a supplied identity and owner binding before it can be navigable.
Nothing here makes a flat task or trace addressable as a conversation.

R001.15 is an execution seam, not an inbox service. The host owns admission,
message identity and durable idempotency/receipts. The harness checks the bound
target and applies messages only before the next model request. It reports the
applied event or a not-applied outcome, with uncertainty explicit after a failed
receipt publication. Completion/cancellation does not redirect input into a new
run. A message consumes the same shared run budget; no authority is expanded.

R001.16 is distinct: an admitted continuation starts a new execution from a
completed child's supported history. It reuses the strict history/checkpoint
selection, applies fresh turn limits and preserves host-supplied enclosing
ceilings. Text-only R001.9.1 does not qualify dependency or recursive history;
R001.8 and R001.9.2.2 remain open for genuine checkpoint composition. History
absence, unsupported form and denied access are not interchangeable with an
empty conversation.

For a host-admitted group executing in one live event loop, the execution
mechanism may receive the same `LiveSharedBudget` object for each run. Every
model request and tool effect reserves against that one object; per-run views
report fresh local counters. The host binds membership and the common deadline,
closes the authority, and drains in-flight work. It supplies an already received
plan; the shared path refuses plan-provider calls and seeded usage so it cannot
silently omit prior planning charges. This in-process capability is not a
durable owner ledger: it refuses checkpoints and cannot cross a process restart
or event loop. With a finite token limit, model calls serialize because
token usage arrives only with the response; a single response may itself exceed
the remaining allowance. Cross-process admission and hard pre-response token
reservations therefore remain unqualified.

R001.17 publishes only explicitly permitted records; a transcript is not
reconstructed from lifecycle events. The host retains owner checks and public
projection. Existing source usage metrics remain authoritative, with no new
client-side token/TPS computation in this work.

Implement in small slices: stable flat-step event projection first; then
explicit nested identity and public records; then safe-boundary active input;
then supported completed-child continuation. Qualify root-child-grandchild,
fan-out/fan-in, replay, foreign identity, cancellation and shared budgets through
the actual bound loop. No new row is delivered by these specifications, and no
EG implementation or native authority work is part of this change.

### 2026-10-25: preserve admitted requests through the client-run seam

R001.11's `harness.hosted_run.iterate_client_run` must accept the host's existing
`RunRequest`, not just its text. Reuse the same request for plan resolution and
`iterate_run`, preserving run identity, context, attachments, limits and the
metadata consumed by R001.9's host-authorized history provider. AU does not
validate a caller's identity or create sessions; Arbora supplies the admitted
request and bound client transport. Reject non-request input before calling a
host provider. Do not infer missing authority from prompt text.

Close the delegated run iterator when its consumer closes, so the existing
cancellation and resource teardown run before this seam returns. This is the
same harness for a central host and an explicitly launched local host; AU adds
no listener, credential source, ACP server or alternative model loop.

The Pydantic harness 0.57.0 experimental ACP adapter drives
`AbstractAgent.run_stream_events` and owns per-session transcript history.
Passing AU's `build_agent` directly to it would skip `iterate_run` request,
history-selection, received-plan, timeout and event handling. This seam change
is a producer prerequisite, not ACP adapter qualification. A later integration
must share AU's run machinery and explicitly qualify streamed execution and
deferred approval passes against those invariants before enabling ACP.

The next R001.11 producer increment accepts an optional awaited native-model
stream observer on that same seam and `iterate_run`. It uses Pydantic AI's
`agent.run(event_stream_handler=...)` inside the existing timeout, tool and
history lifecycle. Observer backpressure consumes the same run deadline;
failure or cancellation does not retry the model or replay an effect. The
observer is trusted host plumbing, not a public transcript: native events can
contain thinking and private tool inputs/results. Existing public run events
remain unchanged; Arbora must explicitly project safe ACP content.

Initially this observer supports ordinary sequential runs only. Dependency and
restricted text-only plans refuse before model execution rather than silently
ignoring the requested observer or widening their qualified execution profile.
FunctionModel streaming tests qualify text deltas, tool transitions, completion
ordering, refusal, cancellation and observer failure. These establish a real
Pydantic streaming path with a scripted model, not external model service or
ACP transport acceptance.

### R001.11 native execution observer propagation

The same optional trusted observer also passes through `run` and
`AgentRunExecutor.execute_agent`. Existing callers retain their signatures and
outcomes when the keyword is omitted. The executor preserves its received
request mapping, exact host session metadata, configured native tool binding,
limits and failure receipt; it changes neither admission nor planning. Each
invocation owns its observer argument; no global observer or alternate run loop
is introduced. Native event privacy and unsupported-plan refusals remain those
of `iterate_run`. Arbora must preserve its native owner scope and public-content
allowlist when composing this producer into an admitted central execution.
### Completed child history selection contract

The checkpoint provider may bind a completed child using `CompletedChildBinding`
with child ID, conversation ID, predecessor run ID, and history revision, each
a nonempty string of at most 512 UTF-8 bytes. The same trusted selection supplies a version-one completed child JSON envelope with
those four fields and the bounded alternating text messages. The harness checks
all four fields against the separate selection binding, validates the existing
completed-text codec, and rejects a predecessor equal to the current run. An
invalid or absent selected payload refuses before planning; it is not an empty
history. Current-run checkpoint precedence is unchanged. The host remains
responsible for authorization and consistent native selection. This local seam
alone does not qualify installed Arbora or native owner acceptance.

### R012 current owner guard

The current harness tree has a checked-in component registry with an explicit
Meristem Python-module inventory and foreign-owner boundary seeds for Arbora,
Scion SDK and Medulla. `scripts/check_boundary_owners.py` checks the inventory
and console-script targets, and rejects a registered module if it also falls
under a foreign seed. It runs in the local hook and hosted gates. Hermetic
checkout fixtures exercise wrong-owner module and script additions, unlisted
modules and stale inventory entries. The registry must be updated when an
owner decision or harness module changes; it does not assert behavioral
parity for the separate relocation requirements.

### R013 current directory coverage

The coverage inventory in this plan lists each present directory at the first two package levels,
its disposition and its owner. The local and hosted coverage guard checks
that inventory against the tree, rejects duplicate or undecided rows and
requires an appropriate destination. Hermetic fixtures demonstrate unlisted
and stale directory failures. This current-tree inventory does not by itself
prove behavioral parity for earlier moves to other repositories.

### R015 current skill-content owner

The current tree contains one skill-content directory,
`meristem/skills/agent-utilities-development/`. It is the repository's
development and release procedure, and remains in Meristem as recorded in
the coverage inventory of this plan. No current skill-content directory is being relocated. The
skill ownership test enumerates the current directories and checks each kept
skill has its own named definition; the coverage guard rejects unrecorded or
stale directories.

### R006.1 connector import census restored

The current-tree census at `scripts/check_connector_import_census.py` inspects
### Consumer rows for medulla decisions (2026-10-30)

Rows MERISTEM-RUN-R003 to MERISTEM-RUN-R006 file the in-run harness obligations of the medulla decision engine. The rows cover cut and ownership contracts for replay sources, study sources and withheld paths. Each rollup row names one medulla row. Each child row names the meristem file, the planned test and the received decision. The harness decides nothing in these rows. Any threshold is a received value with a fallback default. Medulla issue 1029 tracks the source decisions.

## Phase 2: Task approach

One task per row in `tasks.md`; the host-facing rows land before the guards that name their modules.

## Complexity Tracking

No constitution violation.

## Build contract

The gate scripts and census tests listed in `MERISTEM-RUN-R012` to `MERISTEM-RUN-R016` pass with no baseline file.

## Production caller chain

The arbora host operation (`arbora/api/ops/runs.py`) calls `meristem.graph.executor.iterate_run`, which resolves the plan, runs the loop and publishes events through the host ports.

## Cross-repo seams

- `MERISTEM-RUN-R001`: [contracts/arbora.md](contracts/arbora.md)
- `MERISTEM-RUN-R002`: [contracts/medulla.md](contracts/medulla.md)
- `MERISTEM-RUN-R016`: [contracts/scion-sdk.md](contracts/scion-sdk.md)
- `MERISTEM-RUN-R016`: [contracts/repository-manager.md](contracts/repository-manager.md)
