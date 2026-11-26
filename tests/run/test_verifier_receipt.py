"""Adversarial verifier receipts before a submit (MERISTEM-SCM-R006.1)."""

from __future__ import annotations

import ports_support as support
import pytest
from support.coding_fakes import (
    Gates,
    ScriptedEngine,
)
from support.coding_fakes import (
    LocalSource as Source,
)
from support.coding_fakes import (
    local_workspace as workspace,
)

from meristem.harness.adversarial_verifier import artifact_digest
from meristem.harness.changeset_loop import (
    SourceConflict,
    Submitted,
    VerifierSettings,
    run_changeset_cycle,
)
from meristem.harness.edit_engine import Edit

SPEC = "MERISTEM-SCM-R006.1"
# spec: MERISTEM-SCM-R006.1
CASES = (
    {"case_id": "c1", "kind": "boundary", "command": "attack one"},
    {"case_id": "c2", "kind": "fuzz", "command": "attack two"},
)
SETTINGS = VerifierSettings(CASES, ("true",), "policy-1", "verifier-1")
EDITS = (Edit("a.txt", "before", "after"),)
ADMIT = {"outcome": "advisory", "option_ids": ("admit",)}


class Runner(Source, Gates):
    """Real edit application from Source, scripted attack results from Gates."""

    def __init__(self, outcomes, results):
        Source.__init__(self, outcomes)
        self.applied = []
        self.results = results
        self.commands = []

    async def run_gate(self, workspace, command):
        if command == "true":
            return await Source.run_gate(self, workspace, command)
        return await Gates.run_gate(self, workspace, command)


async def _cycle(tmp_path, source, engine):
    support.reset()
    support.register_engine_decision_port(engine)
    return await run_changeset_cycle(
        workspace(tmp_path),
        EDITS,
        base_head="h0",
        run_ref="run",
        fast_gates=("true",),
        submitter=source,
        gate_runner=source,
        ports=support.PORTS,
        verifier=SETTINGS,
    )


@pytest.mark.spec(SPEC)
async def test_each_case_is_retested_against_the_new_digest(tmp_path):
    results = {"attack one": (0, ""), "attack two": (1, "")}
    source = Runner([SourceConflict(("a.txt",)), Submitted("cs-1")], results)
    engine = ScriptedEngine({"decide": [ADMIT, ADMIT]})
    result = await _cycle(tmp_path, source, engine)
    assert result.reason == "submitted"
    first, second = (call[1][1] for call in engine.calls)
    assert [c["state"] for c in first["cases"]] == ["retested", "open"]
    assert first["retest_artifact_digest"] is None
    digest = artifact_digest((("a.txt", "after\n"),))
    assert first["artifact_digest"] == digest
    # The rebase reruns every case, not only the failed one, and binds the new digest.
    assert source.commands.count("attack one") == 2
    assert source.commands.count("attack two") == 2
    assert second["retest_artifact_digest"] == digest


@pytest.mark.spec(SPEC)
async def test_refusal_or_absent_port_or_stale_revision_stops_the_submit(tmp_path):
    results = {"attack one": (0, ""), "attack two": (0, "")}
    source = Runner([Submitted("x")], results)
    stale = {"outcome": "abstained", "refusal_code": "VERIFIER_ARTIFACT_STALE"}
    result = await _cycle(tmp_path, source, ScriptedEngine({"decide": [stale]}))
    assert result.reason == "VERIFIER_ARTIFACT_STALE" and source.submitted == []

    source = Runner([Submitted("x")], results)
    support.reset()
    result = await run_changeset_cycle(
        workspace(tmp_path / "b"),
        EDITS,
        base_head="h0",
        run_ref="run",
        fast_gates=("true",),
        submitter=source,
        gate_runner=source,
        ports=support.PORTS,
        verifier=SETTINGS,
    )
    assert result.reason == "engine_decision_unavailable" and source.submitted == []

    source = Runner([Submitted("x")], results)
    unpinned = VerifierSettings(CASES, ("true",), None, "verifier-1")
    support.reset()
    result = await run_changeset_cycle(
        workspace(tmp_path / "c"),
        EDITS,
        base_head="h0",
        run_ref="run",
        fast_gates=("true",),
        submitter=source,
        gate_runner=source,
        ports=support.PORTS,
        verifier=unpinned,
    )
    assert result.reason == "VERIFIER_REVISION_STALE" and source.submitted == []
