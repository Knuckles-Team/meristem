"""The packaged state-machine ontology describes both machines exactly.

R084.5 and R085.4 stage the in-run run machine and the fan-out task machine
as a flat one-triple-per-line Turtle pack that rolls every class up to the
medulla core vocabulary. The pack is declared by the host at registration;
no module in meristem loads it, so this test parses the file directly with
the standard library. The expected tables are hardcoded from the spec text,
not imported from the implementation.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PACK = (
    Path(__file__).resolve().parents[2]
    / "meristem"
    / "ontology"
    / "run_state_machines.ttl"
)

#: subject, predicate, object — the pack's one-triple-per-line wire form.
_TRIPLE = re.compile(r'^(\S+) (\S+) ("[^"]*"|\S+) \.$')

SPEC_RUN = "MERISTEM-HARNESS-CYCLE-R084.5"
SPEC_TASK = "MERISTEM-HARNESS-CYCLE-R085.4"

#: (state name, wire form, terminal) per the run table of R084.1.
RUN_STATES = {
    "Pending": ("pending", False),
    "Running": ("running", False),
    "Cancelling": ("cancelling", False),
    "Completed": ("completed", True),
    "Failed": ("failed", True),
    "StoppedRequested": ("stopped_requested", True),
    "StoppedBudget": ("stopped_budget", True),
    "StoppedCancelled": ("stopped_cancelled", True),
}

RUN_EVENTS = {
    "Start": "start",
    "Finish": "finish",
    "StopRequested": "stop_requested",
    "StopBudget": "stop_budget",
    "Cancel": "cancel",
    "DrainComplete": "drain_complete",
    "Fail": "fail",
}

#: (edge, from, to, event, guards) — a guardless edge declares owl:Nothing.
RUN_EDGES = {
    "RE1": ("Pending", "Running", "Start", ("Guard_PlanReceived",)),
    "RE2": ("Pending", "Failed", "Fail", ()),
    "RE3": ("Pending", "StoppedCancelled", "Cancel", ()),
    "RE4": ("Running", "Completed", "Finish", ("Guard_OutputTaskComplete",)),
    "RE5": ("Running", "StoppedRequested", "StopRequested", ()),
    "RE6": ("Running", "StoppedBudget", "StopBudget", ()),
    "RE7": ("Running", "Failed", "Fail", ()),
    "RE8": ("Running", "Cancelling", "Cancel", ()),
    "RE9": ("Cancelling", "StoppedCancelled", "DrainComplete", ("Guard_NoActiveTask",)),
    "RE10": ("Cancelling", "Failed", "Fail", ()),
}

RUN_GUARDS = {"Guard_PlanReceived", "Guard_OutputTaskComplete", "Guard_NoActiveTask"}

#: (state name, wire form, terminal) per the task table of R085.1.
TASK_STATES = {
    "Blocked": ("blocked", False),
    "Ready": ("ready", False),
    "Active": ("active", False),
    "Done": ("done", True),
    "Failed": ("failed", True),
    "Cancelled": ("cancelled", True),
    "Skipped": ("skipped", True),
}

TASK_EVENTS = {
    "DepsMet": "deps_met",
    "Dispatch": "dispatch",
    "Complete": "complete",
    "Fail": "fail",
    "Cancel": "cancel",
    "Skip": "skip",
}

#: (edge, from, to, event, guards). The skip edges admit any one of their
#: two guards, so each declares two core:guardedBy values.
TASK_EDGES = {
    "TE1": ("Blocked", "Ready", "DepsMet", ("Guard_AllParentsDone",)),
    "TE2": (
        "Blocked",
        "Skipped",
        "Skip",
        ("Guard_AncestorFailedOrCancelled", "Guard_RunCancellingOrFailed"),
    ),
    "TE3": (
        "Ready",
        "Skipped",
        "Skip",
        ("Guard_AncestorFailedOrCancelled", "Guard_RunCancellingOrFailed"),
    ),
    "TE4": ("Ready", "Active", "Dispatch", ("Guard_PermitHeld",)),
    "TE5": ("Active", "Done", "Complete", ("Guard_RunRunning",)),
    "TE6": ("Active", "Failed", "Fail", ()),
    "TE7": ("Active", "Cancelled", "Cancel", ()),
}


def _triples() -> set[tuple[str, str, str]]:
    triples = set()
    for line in PACK.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("@prefix"):
            continue
        match = _TRIPLE.match(line)
        assert match, f"the pack is one triple per line: {line!r}"
        subject, predicate, object = match.groups()
        triples.add((subject, predicate, object.strip('"')))
    return triples


# spec: MERISTEM-HARNESS-CYCLE-R084.5
@pytest.mark.spec(SPEC_RUN)
def test_every_class_rolls_up_to_a_core_class() -> None:
    triples = _triples()
    expected = {
        (":RunMachine", "rdfs:subClassOf", "core:StateMachine"),
        (":RunStatusState", "rdfs:subClassOf", "core:State"),
        (":RunTerminalState", "rdfs:subClassOf", "core:TerminalState"),
        (":RunTransition", "rdfs:subClassOf", "core:Transition"),
        (":RunEvent", "rdfs:subClassOf", "core:TransitionEvent"),
        (":RunGuard", "rdfs:subClassOf", "core:TransitionGuard"),
    }
    assert expected <= triples
    # The rollup is the only way the classes touch the core vocabulary:
    # no pack class imports the core file.
    assert (":RunMachine", "a", "owl:Class") in triples


@pytest.mark.spec(SPEC_RUN)
def test_pack_has_no_owl_imports() -> None:
    # The pack imports nothing; the classes reach the core vocabulary by
    # rdfs:subClassOf only.
    assert all(predicate != "owl:imports" for _, predicate, _ in _triples())
    assert "@prefix core:" in PACK.read_text(encoding="utf-8")


@pytest.mark.spec(SPEC_RUN)
def test_every_state_and_edge_of_the_table_is_described() -> None:
    triples = _triples()
    for name, (wire, terminal) in RUN_STATES.items():
        subject = f":RunMachine_{name}"
        assert (subject, "a", ":RunStatusState") in triples
        assert (subject, "core:storedForm", wire) in triples
        assert (
            (subject, "a", ":RunTerminalState") in triples
            if terminal
            else ((subject, "a", ":RunTerminalState") not in triples)
        )
    for name, wire in RUN_EVENTS.items():
        subject = f":RunMachine_Event_{name}"
        assert (subject, "a", ":RunEvent") in triples
        assert (subject, "core:storedForm", wire) in triples
    for edge, (source, target, event, guards) in RUN_EDGES.items():
        subject = f":RunMachine_{edge}"
        assert (subject, "a", ":RunTransition") in triples
        assert (subject, "core:fromState", f":RunMachine_{source}") in triples
        assert (subject, "core:toState", f":RunMachine_{target}") in triples
        assert (subject, "core:firedBy", f":RunMachine_Event_{event}") in triples
        if guards:
            for guard in guards:
                assert (subject, "core:guardedBy", f":RunMachine_{guard}") in triples
        else:
            assert (subject, "core:guardedBy", "owl:Nothing") in triples
    # Nothing beyond the table: every machine instance in the pack is a
    # named state, event, guard or edge of the run table.
    subjects = {
        subject for subject, _, _ in triples if subject.startswith(":RunMachine_")
    }
    assert subjects == {f":RunMachine_{edge}" for edge in RUN_EDGES} | {
        f":RunMachine_{state}" for state in RUN_STATES
    } | {f":RunMachine_Event_{event}" for event in RUN_EVENTS} | {
        f":RunMachine_{guard}" for guard in RUN_GUARDS
    }


# spec: MERISTEM-HARNESS-CYCLE-R085.4
@pytest.mark.spec(SPEC_TASK)
def test_every_task_class_rolls_up_to_a_core_class() -> None:
    triples = _triples()
    expected = {
        (":TaskMachine", "rdfs:subClassOf", "core:StateMachine"),
        (":TaskState", "rdfs:subClassOf", "core:State"),
        (":TaskTerminalState", "rdfs:subClassOf", "core:TerminalState"),
        (":TaskTransition", "rdfs:subClassOf", "core:Transition"),
    }
    assert expected <= triples


@pytest.mark.spec(SPEC_TASK)
def test_every_task_state_and_edge_is_described() -> None:
    triples = _triples()
    for name, (wire, terminal) in TASK_STATES.items():
        subject = f":TaskMachine_{name}"
        assert (subject, "a", ":TaskState") in triples
        assert (subject, "core:storedForm", wire) in triples
        assert (
            (subject, "a", ":TaskTerminalState") in triples
            if terminal
            else ((subject, "a", ":TaskTerminalState") not in triples)
        )
    # The task events and guards reuse the run machine's classes.
    for name, wire in TASK_EVENTS.items():
        subject = f":TaskMachine_Event_{name}"
        assert (subject, "a", ":RunEvent") in triples
        assert (subject, "core:storedForm", wire) in triples
    for edge, (source, target, event, guards) in TASK_EDGES.items():
        subject = f":TaskMachine_{edge}"
        assert (subject, "a", ":TaskTransition") in triples
        assert (subject, "core:fromState", f":TaskMachine_{source}") in triples
        assert (subject, "core:toState", f":TaskMachine_{target}") in triples
        assert (subject, "core:firedBy", f":TaskMachine_Event_{event}") in triples
        for guard in guards:
            assert (subject, "core:guardedBy", f":TaskMachine_{guard}") in triples
            assert (f":TaskMachine_{guard}", "a", ":RunGuard") in triples
        if not guards:
            assert (subject, "core:guardedBy", "owl:Nothing") in triples
    # Nothing beyond the table: every task transition is a named edge.
    transitions = {
        subject for subject, _, _ in triples if subject.startswith(":TaskMachine_TE")
    }
    assert transitions == {f":TaskMachine_{edge}" for edge in TASK_EDGES}
