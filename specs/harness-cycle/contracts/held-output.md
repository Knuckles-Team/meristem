# Contract: the held-output block of a completed run

Provider: arbora ARBORA-A2A-R022
Provider: medulla MEDULLA-DECISION-ENGINE-R038.3
Consumer: meristem MERISTEM-HARNESS-CYCLE-R101

The host critique gate holds a generated output until the engine admits the digest of that exact output (`held_item_admission`). The harness emits the block the gate reads. It decides nothing.

## Wire shape

- Source: `result.held_output` of the terminal `graph_complete` event of a completed run, only when the plan sets `HarnessConfig.held_output`.
- Block: `{item_ref, content_digest, engine_decision_ref, checklist: [{id, question}]}`. These are the keys of the host `HeldOutput` type (`heldOutput` on the task metadata, camel-case on the wire).
- `content_digest` is the lowercase SHA-256 hex of the exact output bytes. The host may supply the encoding that turns the output text into the bytes it digests.
- Verification record: `verification: {verifier, compiler, verification_ref}` travels beside the block, only when received. It mirrors the engine `HeldItemAnswer` fields.
- Engine answer shape (`held_item_admission`): `{outcome: admitted|rejected|abstained, verdict_ref, verification_ref, verifier, compiler, reason}`.

## Refusal codes

- The harness refuses nothing here; a run that does not complete carries no block.
- The host and engine refuse with the held-item reasons (`POLICY_EVIDENCE_MISSING`, `DIGEST_MISMATCH`, `VERIFIER_NOT_INDEPENDENT`, `ABSTAINED`). The output stays held.

## Authoritative types

- `meristem/graph/state.py` (`HeldOutputConfig`, `RunResult.held_output`) and `meristem/harness/held_item.py` (`held_output_block`).
- `arbora/a2a/critique_gate.py` (`HeldOutput`, `output_digest`).
- The medulla `HeldItemAnswer` of `medulla/generated/models.py`.

## Test vectors

- `tests/harness/test_held_output_block.py`.
