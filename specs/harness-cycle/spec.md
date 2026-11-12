# MERISTEM-HARNESS-CYCLE — Harness evolution cycle

Owner: meristem. Cross-repo IDs: MEDULLA-ORCH-VOCAB-R801, MEDULLA-ORCH-VOCAB-R802, MEDULLA-DECISION-ENGINE-R803, MEDULLA-DECISION-ENGINE-R136.3, ARBORA-OPS-R068.

## Purpose and user stories

The harness improves itself while a task runs. For one task, the harness proposes candidate component versions. It probes each under identical conditions. It composes the adopted harness from the measured contrast. It persists the result as graph facts. It reflects the lesson for the next task. A developer runs a task through arbora. The developer then reads the adopted harness version, the contrast and the lesson from the graph. The operator sees which component changed, why, and the evidence run behind it.

## Why a new spec

The `harness-evolution` spec was re-homed out of meristem (MERISTEM-HARNESS). The substrate moved to medulla: the versioned components and the improvement library (MEDULLA-ORCH-VOCAB-R801, MEDULLA-ORCH-VOCAB-R802) and the contrastive composition decision (MEDULLA-DECISION-ENGINE-R803). The served read and adoption operations moved to arbora (ARBORA-OPS-R068). This repository keeps the in-run loop itself: the propose, probe, compose, persist and reflect steps between two served operations. `MERISTEM-BOUNDARY` owns the boundary decision. No other spec owns the cycle. The `meristem/harness/` package also holds the constraint, contract, edit, plan and quality-gate engines.

## Scope and interfaces

Owned: the cycle loop and its models (`meristem/graph/harness_cycle.py`). Also owned: the consumer rows R003 onward, where the in-run harness applies received medulla decisions. The probe entry runs through `execute_graph` (`meristem/graph/runner.py:96`). The harness switch runs through `apply_edits` (`meristem/harness/edit_engine.py:457`). Arbora drives the host port. The candidate budget k comes from the stage allocation of MEDULLA-DECISION-ENGINE-R136.3.

Non-goals: versioned component storage and lesson storage (medulla), the served operations (arbora), and any local ranking of candidates. The measured contrast is the authority.

## Open questions

None.

## User Scenarios & Testing

### User Story 1 - Run one evolution tick per task (Priority: P1)

A developer runs a task through arbora. After the run, the harness runs one tick: it proposes k candidates, probes each under the same task, seed, model and budget, and asks the engine to compose. The developer then reads the adopted harness version, the contrast and the lesson from the graph.

**Acceptance Scenarios**:

1. **Given** a task class with a stored lesson and k = 2, **When** the host drives one tick, **Then** the graph shows the adopted version, the contrast and the lesson, and the next propose returns the lesson.
2. **Given** the engine abstains, **When** the tick composes, **Then** the current harness stays and the outcome carries the abstain reason.
3. **Given** no host provider is registered, **When** a tick is requested, **Then** the port fails closed and nothing runs.

### User Story 2 - Apply received engine decisions in the run loop (Priority: P2)

An operator relies on the run loop to apply the decisions the engine makes and to decide nothing itself. Each in-run stage receives its value from a medulla decision, with a fallback default only where the row says so.

**Acceptance Scenarios**:

1. **Given** a recorded engine answer for a stage, **When** the run reaches that stage, **Then** the loop applies exactly the received value and records the decision digest in the trace.
2. **Given** no engine decision port is bound, **When** a consumer stage runs, **Then** it refuses with `engine_decision_unavailable` and decides nothing.

### Edge Cases

- If the improvement library returns no version, the cycle refuses with `no_candidates` and keeps the current harness.
- If k is below the received minimum, the cycle abstains with `budget_below_contrast`.
- If no owner-issued measurement exists, the cycle refuses with `measurement_unavailable` before Decide and before any write.
- If a probe reply names another run, `probe_candidates` refuses it.
- A probe run never starts a tick.

## Requirements

- **FR-001**: Harness evolution cycle. Rows: `MERISTEM-HARNESS-CYCLE-R001`.
- **FR-002**: Consumer of the engine decision R014. Rows: `MERISTEM-HARNESS-CYCLE-R003`.
- **FR-003**: Consumer of the engine decision R020. Rows: `MERISTEM-HARNESS-CYCLE-R004`.
- **FR-004**: Consumer of the engine decision R029. Rows: `MERISTEM-HARNESS-CYCLE-R005`.
- **FR-005**: Consumer of the engine decision R035. Rows: `MERISTEM-HARNESS-CYCLE-R006`.
- **FR-006**: Consumer of the engine decision R121. Rows: `MERISTEM-HARNESS-CYCLE-R007`.
- **FR-007**: Consumer of the engine decision R129. Rows: `MERISTEM-HARNESS-CYCLE-R008`.
- **FR-008**: Consumer of the engine decision R036. Rows: `MERISTEM-HARNESS-CYCLE-R009`.
- **FR-009**: Consumer of the engine decision R046. Rows: `MERISTEM-HARNESS-CYCLE-R010`.
- **FR-010**: Consumer of the engine decision R106. Rows: `MERISTEM-HARNESS-CYCLE-R011`.
- **FR-011**: Consumer of the engine decision R111. Rows: `MERISTEM-HARNESS-CYCLE-R012`.
- **FR-012**: Consumer of the engine decision R172. Rows: `MERISTEM-HARNESS-CYCLE-R013`.
- **FR-013**: Consumer of the engine decision R814. Rows: `MERISTEM-HARNESS-CYCLE-R014`.
- **FR-014**: Consumer of the engine decision R128. Rows: `MERISTEM-HARNESS-CYCLE-R015`.
- **FR-015**: Consumer of the engine decision R133. Rows: `MERISTEM-HARNESS-CYCLE-R016`.
- **FR-016**: Consumer of the engine decision R137. Rows: `MERISTEM-HARNESS-CYCLE-R017`.
- **FR-017**: Consumer of the engine decision R148. Rows: `MERISTEM-HARNESS-CYCLE-R018`.
- **FR-018**: Consumer of the engine decision R158. Rows: `MERISTEM-HARNESS-CYCLE-R019`.
- **FR-019**: Consumer of the engine decision R165. Rows: `MERISTEM-HARNESS-CYCLE-R020`.
- **FR-020**: Consumer of the engine decision R134. Rows: `MERISTEM-HARNESS-CYCLE-R021`.
- **FR-021**: Consumer of the engine decision R135. Rows: `MERISTEM-HARNESS-CYCLE-R022`.
- **FR-022**: Consumer of the engine decision R139. Rows: `MERISTEM-HARNESS-CYCLE-R023`.
- **FR-023**: Consumer of the engine decision R156. Rows: `MERISTEM-HARNESS-CYCLE-R024`.
- **FR-024**: Consumer of the engine decision R159. Rows: `MERISTEM-HARNESS-CYCLE-R025`.
- **FR-025**: Consumer of the engine decision R171. Rows: `MERISTEM-HARNESS-CYCLE-R026`.
- **FR-026**: Consumer of the engine decision R750. Rows: `MERISTEM-HARNESS-CYCLE-R027`.
- **FR-027**: Consumer of the engine decision R751. Rows: `MERISTEM-HARNESS-CYCLE-R028`.
- **FR-028**: Consumer of the engine decision R752. Rows: `MERISTEM-HARNESS-CYCLE-R029`.
- **FR-029**: Consumer of the engine decision R753. Rows: `MERISTEM-HARNESS-CYCLE-R030`.
- **FR-030**: Consumer of the engine decision R144. Rows: `MERISTEM-HARNESS-CYCLE-R031`.
- **FR-031**: Consumer of the engine decision R146. Rows: `MERISTEM-HARNESS-CYCLE-R032`.
- **FR-032**: Consumer of the engine decision R147. Rows: `MERISTEM-HARNESS-CYCLE-R033`.
- **FR-033**: Consumer of the engine decision R149. Rows: `MERISTEM-HARNESS-CYCLE-R034`.
- **FR-034**: Consumer of the engine decision R150. Rows: `MERISTEM-HARNESS-CYCLE-R035`.
- **FR-035**: Consumer of the engine decision R152. Rows: `MERISTEM-HARNESS-CYCLE-R036`.
- **FR-036**: Consumer of the engine decision R153. Rows: `MERISTEM-HARNESS-CYCLE-R037`.
- **FR-037**: Consumer of the engine decision R155. Rows: `MERISTEM-HARNESS-CYCLE-R038`.
- **FR-038**: Consumer of the engine decision R164. Rows: `MERISTEM-HARNESS-CYCLE-R039`.
- **FR-039**: Consumer of the engine decision R651. Rows: `MERISTEM-HARNESS-CYCLE-R040`.
- **FR-040**: Consumer of the engine decision R059. Rows: `MERISTEM-HARNESS-CYCLE-R041`.
- **FR-041**: Consumer of the engine decision R136. Rows: `MERISTEM-HARNESS-CYCLE-R042`.
- **FR-042**: Consumer of the engine decision R654. Rows: `MERISTEM-HARNESS-CYCLE-R043`.
- **FR-043**: Consumer of the engine decision R805. Rows: `MERISTEM-HARNESS-CYCLE-R044`.
- **FR-044**: Consumer of the engine decision R143. Rows: `MERISTEM-HARNESS-CYCLE-R045`.
- **FR-045**: Consumer of the engine decision R653. Rows: `MERISTEM-HARNESS-CYCLE-R046`.
- **FR-046**: Consumer of the engine decision R705. Rows: `MERISTEM-HARNESS-CYCLE-R047`.
- **FR-047**: Consumer of the engine decision R140. Rows: `MERISTEM-HARNESS-CYCLE-R048`.
- **FR-048**: Consumer of the engine decision R142. Rows: `MERISTEM-HARNESS-CYCLE-R049`.
- **FR-049**: Consumer of the engine decision R157. Rows: `MERISTEM-HARNESS-CYCLE-R050`.
- **FR-050**: Consumer of the engine decision R160. Rows: `MERISTEM-HARNESS-CYCLE-R051`.
- **FR-051**: Consumer of the engine decision R168. Rows: `MERISTEM-HARNESS-CYCLE-R052`.
- **FR-052**: Consumer of the engine decision R169. Rows: `MERISTEM-HARNESS-CYCLE-R053`.
- **FR-053**: Run loop refuses tool calls outside the carried subset. Rows: `MERISTEM-HARNESS-CYCLE-R054`.
- **FR-054**: The tool guard asks `arbora.plan/confirm` for an action admission and treats every refusal as deny. Rows: `MERISTEM-HARNESS-CYCLE-R055`.
- **FR-055**: The run loop applies a held item only after an admitted verdict. Rows: `MERISTEM-HARNESS-CYCLE-R056`.
- **FR-056**: Submit the local-model certification receipt to the engine. Rows: `MERISTEM-HARNESS-CYCLE-R057`.
- **FR-057**: Export the harness environment declarations. Rows: `MERISTEM-HARNESS-CYCLE-R058`.
- **FR-058**: Apply a received skill transformation. Rows: `MERISTEM-HARNESS-CYCLE-R059`.
- **FR-059**: Declare child-run lineage in run events. Rows: `MERISTEM-HARNESS-CYCLE-R060`.
- **FR-060**: Record client-monotonic elapsed time per turn. Rows: `MERISTEM-HARNESS-CYCLE-R061`.
- **FR-061**: Emit serving technique benchmark receipts. Rows: `MERISTEM-HARNESS-CYCLE-R062`.
- **FR-062**: Emit liveness evidence and stop a bound process. Rows: `MERISTEM-HARNESS-CYCLE-R063`.
- **FR-063**: Record evolution proposal progress facts. Rows: `MERISTEM-HARNESS-CYCLE-R064`.
- **FR-064**: Run loop requests and applies historical impact risk. Rows: `MERISTEM-HARNESS-CYCLE-R065`.
- **FR-065**: The harness consumes n-ary hypotheses as proposals only. Rows: `MERISTEM-HARNESS-CYCLE-R066`.
- **FR-066**: Run retrieval step submits ContextCompile and renders the bundle. Rows: `MERISTEM-HARNESS-CYCLE-R067`.
- **FR-067**: Invoke the admitted summary provider and mint the receipt. Rows: `MERISTEM-HARNESS-CYCLE-R068`.
- **FR-068**: Apply the received nested input projection before dispatch. Rows: `MERISTEM-HARNESS-CYCLE-R069`.
- **FR-069**: Export parent and child spans with run correlation. Rows: `MERISTEM-HARNESS-CYCLE-R070`.
- **FR-070**: Record and send the run observation window. Rows: `MERISTEM-HARNESS-CYCLE-R071`.
- **FR-071**: Compare replayed step digests with the stored digests. Rows: `MERISTEM-HARNESS-CYCLE-R072`.
- **FR-072**: Produce the code walkthrough manifest for a run. Rows: `MERISTEM-HARNESS-CYCLE-R073`.
- **FR-073**: Ask recruit and allocate and advance instance state. Rows: `MERISTEM-HARNESS-CYCLE-R074`.
- **FR-074**: Report run success only after a matched readback. Rows: `MERISTEM-HARNESS-CYCLE-R075`.
- **FR-075**: Verify the prompt render receipt before model dispatch. Rows: `MERISTEM-HARNESS-CYCLE-R076`.
- **FR-076**: Replay recorded engine answers for consumer run tests. Rows: `MERISTEM-HARNESS-CYCLE-R077`.
- **FR-077**: Run lifecycle hooks called from the run loop. Rows: `MERISTEM-HARNESS-CYCLE-R078`.
- **FR-078**: Extend the engine decision port with served reads and writes. Rows: `MERISTEM-HARNESS-CYCLE-R079`.
- **FR-079**: Condition the next step on a committed observation reference. Rows: `MERISTEM-HARNESS-CYCLE-R082`.
- **FR-080**: Export meristem environment declarations. Rows: `MERISTEM-HARNESS-CYCLE-R083`.

## Success Criteria

- **SC-001**: Harness evolution cycle passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R001`.
- **SC-002**: Consumer of the engine decision R014 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R003`.
- **SC-003**: Consumer of the engine decision R020 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R004`.
- **SC-004**: Consumer of the engine decision R029 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R005`.
- **SC-005**: Consumer of the engine decision R035 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R006`.
- **SC-006**: Consumer of the engine decision R121 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R007`.
- **SC-007**: Consumer of the engine decision R129 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R008`.
- **SC-008**: Consumer of the engine decision R036 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R009`.
- **SC-009**: Consumer of the engine decision R046 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R010`.
- **SC-010**: Consumer of the engine decision R106 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R011`.
- **SC-011**: Consumer of the engine decision R111 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R012`.
- **SC-012**: Consumer of the engine decision R172 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R013`.
- **SC-013**: Consumer of the engine decision R814 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R014`.
- **SC-014**: Consumer of the engine decision R128 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R015`.
- **SC-015**: Consumer of the engine decision R133 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R016`.
- **SC-016**: Consumer of the engine decision R137 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R017`.
- **SC-017**: Consumer of the engine decision R148 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R018`.
- **SC-018**: Consumer of the engine decision R158 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R019`.
- **SC-019**: Consumer of the engine decision R165 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R020`.
- **SC-020**: Consumer of the engine decision R134 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R021`.
- **SC-021**: Consumer of the engine decision R135 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R022`.
- **SC-022**: Consumer of the engine decision R139 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R023`.
- **SC-023**: Consumer of the engine decision R156 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R024`.
- **SC-024**: Consumer of the engine decision R159 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R025`.
- **SC-025**: Consumer of the engine decision R171 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R026`.
- **SC-026**: Consumer of the engine decision R750 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R027`.
- **SC-027**: Consumer of the engine decision R751 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R028`.
- **SC-028**: Consumer of the engine decision R752 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R029`.
- **SC-029**: Consumer of the engine decision R753 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R030`.
- **SC-030**: Consumer of the engine decision R144 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R031`.
- **SC-031**: Consumer of the engine decision R146 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R032`.
- **SC-032**: Consumer of the engine decision R147 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R033`.
- **SC-033**: Consumer of the engine decision R149 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R034`.
- **SC-034**: Consumer of the engine decision R150 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R035`.
- **SC-035**: Consumer of the engine decision R152 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R036`.
- **SC-036**: Consumer of the engine decision R153 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R037`.
- **SC-037**: Consumer of the engine decision R155 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R038`.
- **SC-038**: Consumer of the engine decision R164 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R039`.
- **SC-039**: Consumer of the engine decision R651 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R040`.
- **SC-040**: Consumer of the engine decision R059 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R041`.
- **SC-041**: Consumer of the engine decision R136 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R042`.
- **SC-042**: Consumer of the engine decision R654 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R043`.
- **SC-043**: Consumer of the engine decision R805 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R044`.
- **SC-044**: Consumer of the engine decision R143 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R045`.
- **SC-045**: Consumer of the engine decision R653 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R046`.
- **SC-046**: Consumer of the engine decision R705 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R047`.
- **SC-047**: Consumer of the engine decision R140 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R048`.
- **SC-048**: Consumer of the engine decision R142 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R049`.
- **SC-049**: Consumer of the engine decision R157 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R050`.
- **SC-050**: Consumer of the engine decision R160 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R051`.
- **SC-051**: Consumer of the engine decision R168 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R052`.
- **SC-052**: Consumer of the engine decision R169 passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R053`.
- **SC-053**: Run loop refuses tool calls outside the carried subset passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R054`.
- **SC-054**: The tool guard asks `arbora.plan/confirm` for an action admission and treats every refusal as deny passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R055`.
- **SC-055**: The run loop applies a held item only after an admitted verdict passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R056`.
- **SC-056**: Submit the local-model certification receipt to the engine passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R057`.
- **SC-057**: Export the harness environment declarations passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R058`.
- **SC-058**: Apply a received skill transformation passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R059`.
- **SC-059**: Declare child-run lineage in run events passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R060`.
- **SC-060**: Record client-monotonic elapsed time per turn passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R061`.
- **SC-061**: Emit serving technique benchmark receipts passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R062`.
- **SC-062**: Emit liveness evidence and stop a bound process passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R063`.
- **SC-063**: Record evolution proposal progress facts passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R064`.
- **SC-064**: Run loop requests and applies historical impact risk passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R065`.
- **SC-065**: The harness consumes n-ary hypotheses as proposals only passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R066`.
- **SC-066**: Run retrieval step submits ContextCompile and renders the bundle passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R067`.
- **SC-067**: Invoke the admitted summary provider and mint the receipt passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R068`.
- **SC-068**: Apply the received nested input projection before dispatch passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R069`.
- **SC-069**: Export parent and child spans with run correlation passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R070`.
- **SC-070**: Record and send the run observation window passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R071`.
- **SC-071**: Compare replayed step digests with the stored digests passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R072`.
- **SC-072**: Produce the code walkthrough manifest for a run passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R073`.
- **SC-073**: Ask recruit and allocate and advance instance state passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R074`.
- **SC-074**: Report run success only after a matched readback passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R075`.
- **SC-075**: Verify the prompt render receipt before model dispatch passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R076`.
- **SC-076**: Replay recorded engine answers for consumer run tests passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R077`.
- **SC-077**: Run lifecycle hooks called from the run loop passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R078`.
- **SC-078**: Extend the engine decision port with served reads and writes passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R079`.
- **SC-079**: Condition the next step on a committed observation reference passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R082`.
- **SC-080**: Export meristem environment declarations passes its bound test. Rows: `MERISTEM-HARNESS-CYCLE-R083`.

## Assumptions

- The arbora host registers the cycle and probe providers and binds the engine decision port; until it does, the cycle refuses.
- The engine supplies a verifiable measurement and adoption receipt before the cycle composes or persists an adoption.
- The SDK and engine contracts the rows cite are at the floors declared in `pyproject.toml`.

## Traceability

| Label | Rows | Design section | Test ID | Evidence |
|---|---|---|---|---|
| FR-001 | `MERISTEM-HARNESS-CYCLE-R001` | [plan.md](plan.md) | T-001 |  |
| FR-002 | `MERISTEM-HARNESS-CYCLE-R003` | [plan.md](plan.md) | T-016 |  |
| FR-003 | `MERISTEM-HARNESS-CYCLE-R004` | [plan.md](plan.md) | T-017 |  |
| FR-004 | `MERISTEM-HARNESS-CYCLE-R005` | [plan.md](plan.md) | T-018 |  |
| FR-005 | `MERISTEM-HARNESS-CYCLE-R006` | [plan.md](plan.md) | T-020 |  |
| FR-006 | `MERISTEM-HARNESS-CYCLE-R007` | [plan.md](plan.md) | T-022 |  |
| FR-007 | `MERISTEM-HARNESS-CYCLE-R008` | [plan.md](plan.md) | T-023 |  |
| FR-008 | `MERISTEM-HARNESS-CYCLE-R009` | [plan.md](plan.md) | T-024 |  |
| FR-009 | `MERISTEM-HARNESS-CYCLE-R010` | [plan.md](plan.md) | T-025 |  |
| FR-010 | `MERISTEM-HARNESS-CYCLE-R011` | [plan.md](plan.md) | T-026 |  |
| FR-011 | `MERISTEM-HARNESS-CYCLE-R012` | [plan.md](plan.md) | T-027 |  |
| FR-012 | `MERISTEM-HARNESS-CYCLE-R013` | [plan.md](plan.md) | T-028 |  |
| FR-013 | `MERISTEM-HARNESS-CYCLE-R014` | [plan.md](plan.md) | T-030 |  |
| FR-014 | `MERISTEM-HARNESS-CYCLE-R015` | [plan.md](plan.md) | T-031 |  |
| FR-015 | `MERISTEM-HARNESS-CYCLE-R016` | [plan.md](plan.md) | T-032 |  |
| FR-016 | `MERISTEM-HARNESS-CYCLE-R017` | [plan.md](plan.md) | T-035 |  |
| FR-017 | `MERISTEM-HARNESS-CYCLE-R018` | [plan.md](plan.md) | T-041 |  |
| FR-018 | `MERISTEM-HARNESS-CYCLE-R019` | [plan.md](plan.md) | T-042 |  |
| FR-019 | `MERISTEM-HARNESS-CYCLE-R020` | [plan.md](plan.md) | T-043 |  |
| FR-020 | `MERISTEM-HARNESS-CYCLE-R021` | [plan.md](plan.md) | T-044 |  |
| FR-021 | `MERISTEM-HARNESS-CYCLE-R022` | [plan.md](plan.md) | T-053 |  |
| FR-022 | `MERISTEM-HARNESS-CYCLE-R023` | [plan.md](plan.md) | T-056 |  |
| FR-023 | `MERISTEM-HARNESS-CYCLE-R024` | [plan.md](plan.md) | T-057 |  |
| FR-024 | `MERISTEM-HARNESS-CYCLE-R025` | [plan.md](plan.md) | T-058 |  |
| FR-025 | `MERISTEM-HARNESS-CYCLE-R026` | [plan.md](plan.md) | T-059 |  |
| FR-026 | `MERISTEM-HARNESS-CYCLE-R027` | [plan.md](plan.md) | T-060 |  |
| FR-027 | `MERISTEM-HARNESS-CYCLE-R028` | [plan.md](plan.md) | T-061 |  |
| FR-028 | `MERISTEM-HARNESS-CYCLE-R029` | [plan.md](plan.md) | T-062 |  |
| FR-029 | `MERISTEM-HARNESS-CYCLE-R030` | [plan.md](plan.md) | T-063 |  |
| FR-030 | `MERISTEM-HARNESS-CYCLE-R031` | [plan.md](plan.md) | T-064 |  |
| FR-031 | `MERISTEM-HARNESS-CYCLE-R032` | [plan.md](plan.md) | T-065 |  |
| FR-032 | `MERISTEM-HARNESS-CYCLE-R033` | [plan.md](plan.md) | T-066 |  |
| FR-033 | `MERISTEM-HARNESS-CYCLE-R034` | [plan.md](plan.md) | T-067 |  |
| FR-034 | `MERISTEM-HARNESS-CYCLE-R035` | [plan.md](plan.md) | T-069 |  |
| FR-035 | `MERISTEM-HARNESS-CYCLE-R036` | [plan.md](plan.md) | T-070 |  |
| FR-036 | `MERISTEM-HARNESS-CYCLE-R037` | [plan.md](plan.md) | T-072 |  |
| FR-037 | `MERISTEM-HARNESS-CYCLE-R038` | [plan.md](plan.md) | T-073 |  |
| FR-038 | `MERISTEM-HARNESS-CYCLE-R039` | [plan.md](plan.md) | T-074 |  |
| FR-039 | `MERISTEM-HARNESS-CYCLE-R040` | [plan.md](plan.md) | T-075 |  |
| FR-040 | `MERISTEM-HARNESS-CYCLE-R041` | [plan.md](plan.md) | T-076 |  |
| FR-041 | `MERISTEM-HARNESS-CYCLE-R042` | [plan.md](plan.md) | T-078 |  |
| FR-042 | `MERISTEM-HARNESS-CYCLE-R043` | [plan.md](plan.md) | T-079 |  |
| FR-043 | `MERISTEM-HARNESS-CYCLE-R044` | [plan.md](plan.md) | T-080 |  |
| FR-044 | `MERISTEM-HARNESS-CYCLE-R045` | [plan.md](plan.md) | T-081 |  |
| FR-045 | `MERISTEM-HARNESS-CYCLE-R046` | [plan.md](plan.md) | T-082 |  |
| FR-046 | `MERISTEM-HARNESS-CYCLE-R047` | [plan.md](plan.md) | T-083 |  |
| FR-047 | `MERISTEM-HARNESS-CYCLE-R048` | [plan.md](plan.md) | T-084 |  |
| FR-048 | `MERISTEM-HARNESS-CYCLE-R049` | [plan.md](plan.md) | T-088 |  |
| FR-049 | `MERISTEM-HARNESS-CYCLE-R050` | [plan.md](plan.md) | T-089 |  |
| FR-050 | `MERISTEM-HARNESS-CYCLE-R051` | [plan.md](plan.md) | T-090 |  |
| FR-051 | `MERISTEM-HARNESS-CYCLE-R052` | [plan.md](plan.md) | T-091 |  |
| FR-052 | `MERISTEM-HARNESS-CYCLE-R053` | [plan.md](plan.md) | T-092 |  |
| FR-053 | `MERISTEM-HARNESS-CYCLE-R054` | [plan.md](plan.md) | T-093 |  |
| FR-054 | `MERISTEM-HARNESS-CYCLE-R055` | [plan.md](plan.md) | T-094 |  |
| FR-055 | `MERISTEM-HARNESS-CYCLE-R056` | [plan.md](plan.md) | T-095 |  |
| FR-056 | `MERISTEM-HARNESS-CYCLE-R057` | [plan.md](plan.md) | T-096 |  |
| FR-057 | `MERISTEM-HARNESS-CYCLE-R058` | [plan.md](plan.md) | T-097 |  |
| FR-058 | `MERISTEM-HARNESS-CYCLE-R059` | [plan.md](plan.md) | T-098 |  |
| FR-059 | `MERISTEM-HARNESS-CYCLE-R060` | [plan.md](plan.md) | T-099 |  |
| FR-060 | `MERISTEM-HARNESS-CYCLE-R061` | [plan.md](plan.md) | T-100 |  |
| FR-061 | `MERISTEM-HARNESS-CYCLE-R062` | [plan.md](plan.md) | T-101 |  |
| FR-062 | `MERISTEM-HARNESS-CYCLE-R063` | [plan.md](plan.md) | T-102 |  |
| FR-063 | `MERISTEM-HARNESS-CYCLE-R064` | [plan.md](plan.md) | T-103 |  |
| FR-064 | `MERISTEM-HARNESS-CYCLE-R065` | [plan.md](plan.md) | T-104 |  |
| FR-065 | `MERISTEM-HARNESS-CYCLE-R066` | [plan.md](plan.md) | T-105 |  |
| FR-066 | `MERISTEM-HARNESS-CYCLE-R067` | [plan.md](plan.md) | T-106 |  |
| FR-067 | `MERISTEM-HARNESS-CYCLE-R068` | [plan.md](plan.md) | T-107 |  |
| FR-068 | `MERISTEM-HARNESS-CYCLE-R069` | [plan.md](plan.md) | T-108 |  |
| FR-069 | `MERISTEM-HARNESS-CYCLE-R070` | [plan.md](plan.md) | T-109 |  |
| FR-070 | `MERISTEM-HARNESS-CYCLE-R071` | [plan.md](plan.md) | T-110 |  |
| FR-071 | `MERISTEM-HARNESS-CYCLE-R072` | [plan.md](plan.md) | T-111 |  |
| FR-072 | `MERISTEM-HARNESS-CYCLE-R073` | [plan.md](plan.md) | T-112 |  |
| FR-073 | `MERISTEM-HARNESS-CYCLE-R074` | [plan.md](plan.md) | T-113 |  |
| FR-074 | `MERISTEM-HARNESS-CYCLE-R075` | [plan.md](plan.md) | T-114 |  |
| FR-075 | `MERISTEM-HARNESS-CYCLE-R076` | [plan.md](plan.md) | T-115 |  |
| FR-076 | `MERISTEM-HARNESS-CYCLE-R077` | [plan.md](plan.md) | T-116 |  |
| FR-077 | `MERISTEM-HARNESS-CYCLE-R078` | [plan.md](plan.md) | T-117 |  |
| FR-078 | `MERISTEM-HARNESS-CYCLE-R079` | [plan.md](plan.md) | T-118 |  |
| FR-079 | `MERISTEM-HARNESS-CYCLE-R082` | [plan.md](plan.md) | T-119 |  |
| FR-080 | `MERISTEM-HARNESS-CYCLE-R083` | [plan.md](plan.md) | T-120 |  |
