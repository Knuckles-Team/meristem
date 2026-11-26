"""One delivery assignment run applies received feedback (MERISTEM-SCM-R012).

The host starts the assignment and delivers its inputs (CI results, review
findings, follow-ups). The run iterates the same proposal and returns one report
per turn. It picks no work, never arms or merges a proposal and never edits a
spec; the engine decides the arming verdict and owns the delivery records.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from meristem.harness import engine_step
from meristem.harness.changeset_loop import (
    ChangesetSubmitter,
    CycleResult,
    SandboxGateRunner,
    run_changeset_cycle,
)
from meristem.harness.edit_engine import Edit
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import ActiveChildInputPort, ActiveChildMessage, HostPorts
from meristem.runtime.workspace import DevWorkspace

REPORT_STATES = frozenset(
    {"progress", "waiting", "ready", "blocked", "failed", "dropped"}
)
DEFAULT_BACKGROUND_LIMIT_S = 1800
CONNECTOR = "repository-manager"


@dataclass(frozen=True)
class DeliveryAssignmentContext:
    """Every field is a received value; the harness decides none of them."""

    delivery_id: str
    scope_rows: tuple[str, ...] = ()
    proposal_ref: str | None = None
    inputs: tuple[Mapping[str, Any], ...] = ()
    recovery_note: str | None = None
    round_verdicts: Mapping[str, str] = field(default_factory=dict)
    background_limit_s: int | None = None


@dataclass(frozen=True)
class DeliveryReportOut:
    """One turn's report; ``state`` is a member of the closed set."""

    state: str
    summary: str = ""
    proposal_ref: str | None = None
    question: str | None = None

    def __post_init__(self) -> None:
        if self.state not in REPORT_STATES:
            raise ConsumerRefused("delivery_report_state_unknown")


@dataclass
class DeliveryRun:
    """The live state of one assignment: terminal flag, question, landed effects."""

    context: DeliveryAssignmentContext
    ports: HostPorts
    terminal: bool = False
    question: str | None = None
    landed: set[str] = field(default_factory=set)
    proposal_exists: bool = False


async def deliver_inputs(
    run: DeliveryRun,
    input_port: ActiveChildInputPort,
    apply: Callable[[ActiveChildMessage], Awaitable[None]],
) -> int:
    """Apply newly delivered inputs between model turns, in receipt order (R012.2).

    Each input is marked applied through the host port after ``apply`` returns.
    An input that arrives after a terminal report refuses
    ``delivery_input_after_terminal``.
    """
    messages = await input_port.take_ready()
    if messages and run.terminal:
        raise ConsumerRefused("delivery_input_after_terminal")
    for message in messages:
        await apply(message)
        await input_port.report_applied(message)
    return len(messages)


async def revise_proposal(
    run: DeliveryRun,
    workspace: DevWorkspace,
    edits: tuple[Edit, ...],
    *,
    base_head: str,
    fast_gates: tuple[str, ...],
    submitter: ChangesetSubmitter | None,
    gate_runner: SandboxGateRunner | None,
    run_ref: str,
) -> CycleResult:
    """One more changeset cycle on the delivery branch of the same proposal (R012.3).

    The merge-arming verdict is asked first through the engine decision port. A
    missing verdict refuses ``delivery_arming_verdict_missing`` and pushes
    nothing; a ``disarm`` verdict is applied before the push as a run event.
    """
    context = run.context
    answer = await engine_step.decide(
        run.ports.engine_decision_port(),
        "merge_arming",
        {"delivery_id": context.delivery_id, "proposal_ref": context.proposal_ref},
        run_ref=run_ref,
    )
    verdict = answer.payload.get("verdict")
    if verdict not in ("arm", "disarm"):
        raise ConsumerRefused("delivery_arming_verdict_missing")
    if verdict == "disarm":
        run.ports.emit_run_event(
            {"kind": "delivery_disarm", "proposal_ref": context.proposal_ref}
        )
    result = await run_changeset_cycle(
        workspace,
        edits,
        base_head=base_head,
        run_ref=run_ref,
        fast_gates=fast_gates,
        submitter=submitter,
        gate_runner=gate_runner,
        ports=run.ports,
    )
    if result.reason == "submitted":
        run.proposal_exists = True
    return result


async def dispose_thread(
    run: DeliveryRun,
    finding: Mapping[str, Any],
    outcome: str,
    *,
    reviewer_is_automated: bool = False,
    reply_reason: str = "",
) -> str | None:
    """Take one disposition for one review finding (R012.4); returns an observation.

    ``fixed`` resolves the thread; ``declined`` replies once with a reason after
    looking for an earlier reply. A ``stop_addressing`` round verdict ends work
    on an automated reviewer's findings. Any other outcome leaves the thread
    open. A refused host call returns ``delivery_disposition_refused``.
    """
    caller = run.ports.tool_caller()
    thread = str(finding["thread_id"])
    reviewer = str(finding.get("reviewer", ""))
    if (
        reviewer_is_automated
        and run.context.round_verdicts.get(reviewer) == "stop_addressing"
    ):
        return "stop_addressing"
    if outcome not in ("fixed", "declined") or caller is None:
        return None
    key = f"{outcome}:{thread}"
    if key in run.landed:
        return None
    try:
        if outcome == "fixed":
            result = await caller(
                CONNECTOR, "thread.resolve", {"thread_id": thread}, key
            )
        else:
            earlier = await caller(
                CONNECTOR, "thread.replies", {"thread_id": thread}, key
            )
            if earlier.outcome == "ok" and earlier.result:
                run.landed.add(key)
                return None
            result = await caller(
                CONNECTOR,
                "thread.reply",
                {"thread_id": thread, "body": reply_reason},
                key,
            )
    except Exception:  # noqa: BLE001 - a refused disposition is an observation
        return "delivery_disposition_refused"
    if result.outcome != "ok":
        return "delivery_disposition_refused"
    run.landed.add(key)
    return None


def report(
    run: DeliveryRun,
    state: str,
    summary: str = "",
    *,
    question: str | None = None,
    reviewed: bool = False,
    gates_passed: bool = False,
    background_running: Sequence[str] = (),
    proposal_ref: str | None = None,
) -> DeliveryReportOut:
    """End a turn with one report, emitted through the run event sink (R012.5)."""
    proposal = proposal_ref or run.context.proposal_ref
    if proposal is None and run.proposal_exists:
        raise ConsumerRefused("delivery_report_proposal_missing")
    if state == "ready" and (not reviewed or not gates_passed or background_running):
        raise ConsumerRefused("delivery_report_not_ready")
    if state in ("blocked", "failed") and not summary.strip():
        raise ConsumerRefused("delivery_report_prerequisite_missing")
    if question is not None:
        run.question = question
    out = DeliveryReportOut(state, summary, proposal, run.question)
    run.terminal = state in ("ready", "dropped", "failed")
    run.ports.emit_run_event({"kind": "delivery_report", **out.__dict__})
    return out


async def reconcile(run: DeliveryRun) -> set[str]:
    """Read the proposal and the changeset log before any outside effect (R012.6).

    Returns the effects that already took effect so none repeats. A failed read
    refuses ``delivery_recovery_read_failed``; the caller reports ``blocked``.
    """
    context = run.context
    if not context.recovery_note:
        return set()
    caller = run.ports.tool_caller()
    if caller is None:
        raise ConsumerRefused("delivery_recovery_read_failed")
    try:
        proposal = await caller(
            CONNECTOR,
            "proposal.read",
            {"proposal_ref": context.proposal_ref},
            "recover:p",
        )
        log = await caller(
            CONNECTOR,
            "changeset.log",
            {"proposal_ref": context.proposal_ref},
            "recover:l",
        )
    except Exception:  # noqa: BLE001 - a failed read is a closed refusal
        raise ConsumerRefused("delivery_recovery_read_failed") from None
    if proposal.outcome != "ok" or log.outcome != "ok":
        raise ConsumerRefused("delivery_recovery_read_failed")
    done = {str(effect) for effect in (log.result or {}).get("effects", ())}
    run.landed |= done
    return done


async def drain_background(
    run: DeliveryRun,
    jobs: Sequence[str],
    wait: Callable[[Sequence[str], float], Awaitable[set[str]]],
    stop: Callable[[str], Awaitable[None]],
) -> DeliveryReportOut | None:
    """Let background jobs run for the received limit, then stop the rest (R012.7).

    ``wait(jobs, limit_s)`` returns the names that ended within the limit. Any
    job still running is stopped and reported as ``waiting`` with its name.
    """
    limit = run.context.background_limit_s
    limit = DEFAULT_BACKGROUND_LIMIT_S if limit is None else limit
    if type(limit) is not int or limit <= 0:
        raise ConsumerRefused("delivery_background_limit_invalid")
    ended = await wait(jobs, float(limit))
    remaining = [name for name in jobs if name not in ended]
    for name in remaining:
        await stop(name)
    if not remaining:
        return None
    return report(run, "waiting", "background jobs stopped: " + ", ".join(remaining))


__all__ = [
    "DEFAULT_BACKGROUND_LIMIT_S",
    "DeliveryAssignmentContext",
    "DeliveryReportOut",
    "DeliveryRun",
    "deliver_inputs",
    "dispose_thread",
    "drain_background",
    "reconcile",
    "report",
    "revise_proposal",
]
