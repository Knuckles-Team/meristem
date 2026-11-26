"""Adversarial verifier receipts before a changeset submit (MERISTEM-SCM-R006.1).

The harness runs each received case, reports the observed states and sends the
receipt to the engine decision port; it submits only on an ``admit`` answer.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineDecisionPort


@dataclass(frozen=True)
class VerifierCase:
    case_id: str
    kind: str
    command: str
    state: str = "open"


@dataclass(frozen=True)
class VerifierReceipt:
    cases: tuple[VerifierCase, ...]
    artifact_digest: str
    tests_digest: str
    attack_policy_revision: str
    verifier_revision: str
    retest_artifact_digest: str | None = None


def _sha(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def artifact_digest(files: Sequence[tuple[str, str]]) -> str:
    """SHA-256 over the sorted path and file-digest pairs of the changed files."""
    pairs = sorted((path, _sha(content)) for path, content in files)
    return _sha(json.dumps(pairs, separators=(",", ":")))


async def _run_all(
    cases: Sequence[VerifierCase], run_gate: Callable[[str], Any]
) -> tuple[VerifierCase, ...]:
    done = []
    for case in cases:
        observation = await run_gate(case.command)
        done.append(
            replace(case, state="retested" if observation.exit_code == 0 else "open")
        )
    return tuple(done)


async def run_adversarial_verification(
    cases: Sequence[Mapping[str, Any]],
    run_gate: Callable[[str], Any],
    files: Sequence[tuple[str, str]],
    tests: Sequence[str],
    *,
    attack_policy_revision: str | None,
    verifier_revision: str | None,
) -> VerifierReceipt:
    """Run every received case once and build the receipt of what was observed."""
    if not attack_policy_revision or not verifier_revision:
        raise ConsumerRefused("VERIFIER_REVISION_STALE")
    ran = await _run_all(
        [
            VerifierCase(str(c["case_id"]), str(c["kind"]), str(c["command"]))
            for c in cases
        ],
        run_gate,
    )
    return VerifierReceipt(
        ran,
        artifact_digest(files),
        _sha(json.dumps(list(tests))),
        attack_policy_revision,
        verifier_revision,
    )


async def retest_cases(
    receipt: VerifierReceipt,
    run_gate: Callable[[str], Any],
    files: Sequence[tuple[str, str]],
) -> VerifierReceipt:
    """After a fix, run every case again, not only the failed ones."""
    digest = artifact_digest(files)
    ran = await _run_all(receipt.cases, run_gate)
    return replace(receipt, cases=ran, retest_artifact_digest=digest)


async def admit_receipt(
    receipt: VerifierReceipt,
    port: EngineDecisionPort | None,
    *,
    run_ref: str,
    emit: Callable[[Mapping[str, Any]], None],
) -> str | None:
    """Ask for admission; ``None`` means submit, otherwise the closed refusal code."""
    if port is None:
        return "engine_decision_unavailable"
    body = {
        "cases": [
            {"case_id": c.case_id, "kind": c.kind, "state": c.state}
            for c in receipt.cases
        ],
        "artifact_digest": receipt.artifact_digest,
        "retest_artifact_digest": receipt.retest_artifact_digest,
        "tests_digest": receipt.tests_digest,
        "attack_policy_revision": receipt.attack_policy_revision,
        "verifier_revision": receipt.verifier_revision,
    }
    answer = await port.decide("GeneratedCodeAdmission", body, run_ref=run_ref)
    emit(
        {
            "kind": "verifier_receipt_submitted",
            "run_ref": run_ref,
            "receipt_digest": _sha(json.dumps(body, sort_keys=True)),
        }
    )
    if answer.refusal_code is not None:
        return answer.refusal_code
    return None if "admit" in answer.option_ids else "verifier_not_admitted"
