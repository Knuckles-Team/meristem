# Medulla seam contract

Provider: medulla MEDULLA-DECISION-ENGINE-R803
Consumer: meristem MERISTEM-HARNESS-CYCLE-R001

Medulla measures the contrast between probe traces and decides each adoption. Meristem proposes candidates, runs the probes, sends the trace references, applies the answer and reflects the lesson. Meristem makes no ranking and holds no threshold.

## Decisions

| Capability | Owner | Decision |
|---|---|---|
| Versioned harness components and the improvement library | medulla | Read through the generated client (MEDULLA-ORCH-VOCAB-R801, MEDULLA-ORCH-VOCAB-R802). The cycle stores nothing. |
| Contrast measurement and adoption decision | medulla | One served `Decide` under the `HARNESS_CONTRAST` question kind (MEDULLA-DECISION-ENGINE-R803). Meristem sends trace references only. |
| Candidate budget k and its bounds | medulla | Received from the stage allocation (MEDULLA-DECISION-ENGINE-R136.3) and the knobs `harness_evolution.min_candidates` and `harness_evolution.max_candidates` (MEDULLA-DECISION-ENGINE-R179.7). The local minimum is a fallback default. |
| Adoption, contrast and lesson records | medulla | One `DecisionLog` write (MEDULLA-DECISION-ENGINE-R803.8). Meristem has no other write path. |
| In-run consumer decisions | medulla | Each consumer rollup row below applies one engine decision it receives. |

## Allowed call direction

Meristem calls medulla only through the engine decision port the host binds. Medulla never calls meristem.

## Wire shape

The request is a `ContrastRequest` (`meristem/harness/harness_cycle.py:275`) with candidate and trace references and the conditions digest. The answer is a `ContrastAnswer` (`harness_cycle.py:287`) that adopts one candidate or abstains. The persist step sends the adoption, the contrast and the lesson as one `DecisionLog` entry. The decision port that carries both is not in the tree yet (rows R001.10 and R079).

## Refusal codes

- `measurement_unavailable`: no engine decision port, or no owner-issued measurement; the cycle keeps the current harness.
- `no_candidates`: the improvement library returned no version for the task class.
- `budget_below_contrast`: k is below the received minimum.
- Engine codes pass through unchanged.

## Authoritative types

- Meristem: `meristem/harness/harness_cycle.py` (`HarnessCandidate`, `CycleOutcome`, `ContrastRequest`, `ContrastAnswer`, `CycleRefusal`).
- Medulla: the generated client and the rows named here; meristem copies none of those types.

## Test vectors

- `tests/harness/test_harness_cycle.py` runs the step vectors with injected ports.
- `tests/harness/test_harness_cycle_served.py` (planned, R001.9) replays the recorded engine answer of the served medulla test of MEDULLA-DECISION-ENGINE-R803.6.

## Consumer rows

| Meristem row | Medulla row |
|---|---|
| MERISTEM-HARNESS-CYCLE-R003 | MEDULLA-DECISION-ENGINE-R014 |
| MERISTEM-HARNESS-CYCLE-R004 | MEDULLA-DECISION-ENGINE-R020 |
| MERISTEM-HARNESS-CYCLE-R005 | MEDULLA-DECISION-ENGINE-R029 |
| MERISTEM-HARNESS-CYCLE-R006 | MEDULLA-DECISION-ENGINE-R035 |
| MERISTEM-HARNESS-CYCLE-R007 | MEDULLA-DECISION-ENGINE-R121 |
| MERISTEM-HARNESS-CYCLE-R008 | MEDULLA-DECISION-ENGINE-R129 |
| MERISTEM-HARNESS-CYCLE-R009 | MEDULLA-DECISION-ENGINE-R036 |
| MERISTEM-HARNESS-CYCLE-R010 | MEDULLA-DECISION-ENGINE-R046 |
| MERISTEM-HARNESS-CYCLE-R011 | MEDULLA-DECISION-ENGINE-R106 |
| MERISTEM-HARNESS-CYCLE-R012 | MEDULLA-DECISION-ENGINE-R111 |
| MERISTEM-HARNESS-CYCLE-R013 | MEDULLA-DECISION-ENGINE-R172 |
| MERISTEM-HARNESS-CYCLE-R014 | MEDULLA-DECISION-ENGINE-R814 |
| MERISTEM-HARNESS-CYCLE-R015 | MEDULLA-DECISION-ENGINE-R128 |
| MERISTEM-HARNESS-CYCLE-R016 | MEDULLA-DECISION-ENGINE-R133 |
| MERISTEM-HARNESS-CYCLE-R017 | MEDULLA-DECISION-ENGINE-R137 |
| MERISTEM-HARNESS-CYCLE-R018 | MEDULLA-DECISION-ENGINE-R148 |
| MERISTEM-HARNESS-CYCLE-R019 | MEDULLA-DECISION-ENGINE-R158 |
| MERISTEM-HARNESS-CYCLE-R020 | MEDULLA-DECISION-ENGINE-R165 |
| MERISTEM-HARNESS-CYCLE-R021 | MEDULLA-DECISION-ENGINE-R134 |
| MERISTEM-HARNESS-CYCLE-R022 | MEDULLA-DECISION-ENGINE-R135 |
| MERISTEM-HARNESS-CYCLE-R023 | MEDULLA-DECISION-ENGINE-R139 |
| MERISTEM-HARNESS-CYCLE-R024 | MEDULLA-DECISION-ENGINE-R156 |
| MERISTEM-HARNESS-CYCLE-R025 | MEDULLA-DECISION-ENGINE-R159 |
| MERISTEM-HARNESS-CYCLE-R026 | MEDULLA-DECISION-ENGINE-R171 |
| MERISTEM-HARNESS-CYCLE-R027 | MEDULLA-DECISION-ENGINE-R750 |
| MERISTEM-HARNESS-CYCLE-R028 | MEDULLA-DECISION-ENGINE-R751 |
| MERISTEM-HARNESS-CYCLE-R029 | MEDULLA-DECISION-ENGINE-R752 |
| MERISTEM-HARNESS-CYCLE-R030 | MEDULLA-DECISION-ENGINE-R753 |
| MERISTEM-HARNESS-CYCLE-R031 | MEDULLA-DECISION-ENGINE-R144 |
| MERISTEM-HARNESS-CYCLE-R032 | MEDULLA-DECISION-ENGINE-R146 |
| MERISTEM-HARNESS-CYCLE-R033 | MEDULLA-DECISION-ENGINE-R147 |
| MERISTEM-HARNESS-CYCLE-R034 | MEDULLA-DECISION-ENGINE-R149 |
| MERISTEM-HARNESS-CYCLE-R035 | MEDULLA-DECISION-ENGINE-R150 |
| MERISTEM-HARNESS-CYCLE-R036 | MEDULLA-DECISION-ENGINE-R152 |
| MERISTEM-HARNESS-CYCLE-R037 | MEDULLA-DECISION-ENGINE-R153 |
| MERISTEM-HARNESS-CYCLE-R038 | MEDULLA-DECISION-ENGINE-R155 |
| MERISTEM-HARNESS-CYCLE-R039 | MEDULLA-DECISION-ENGINE-R164 |
| MERISTEM-HARNESS-CYCLE-R040 | MEDULLA-DECISION-ENGINE-R651 |
| MERISTEM-HARNESS-CYCLE-R041 | MEDULLA-DECISION-ENGINE-R059 |
| MERISTEM-HARNESS-CYCLE-R042 | MEDULLA-DECISION-ENGINE-R136 |
| MERISTEM-HARNESS-CYCLE-R043 | MEDULLA-DECISION-ENGINE-R654 |
| MERISTEM-HARNESS-CYCLE-R044 | MEDULLA-DECISION-ENGINE-R805 |
| MERISTEM-HARNESS-CYCLE-R045 | MEDULLA-DECISION-ENGINE-R143 |
| MERISTEM-HARNESS-CYCLE-R046 | MEDULLA-DECISION-ENGINE-R653 |
| MERISTEM-HARNESS-CYCLE-R047 | MEDULLA-DECISION-ENGINE-R705 |
| MERISTEM-HARNESS-CYCLE-R048 | MEDULLA-DECISION-ENGINE-R140 |
| MERISTEM-HARNESS-CYCLE-R049 | MEDULLA-DECISION-ENGINE-R142 |
| MERISTEM-HARNESS-CYCLE-R050 | MEDULLA-DECISION-ENGINE-R157 |
| MERISTEM-HARNESS-CYCLE-R051 | MEDULLA-DECISION-ENGINE-R160 |
| MERISTEM-HARNESS-CYCLE-R052 | MEDULLA-DECISION-ENGINE-R168 |
| MERISTEM-HARNESS-CYCLE-R053 | MEDULLA-DECISION-ENGINE-R169 |

## Implementing specs

| Side | Spec IDs |
|---|---|
| Provider | MEDULLA-DECISION-ENGINE-R803, MEDULLA-DECISION-ENGINE-R136.3, MEDULLA-DECISION-ENGINE-R179.7, MEDULLA-ORCH-VOCAB-R801, MEDULLA-ORCH-VOCAB-R802 |
| Consumer | MERISTEM-HARNESS-CYCLE-R001, MERISTEM-HARNESS-CYCLE-R003 to MERISTEM-HARNESS-CYCLE-R053 |
