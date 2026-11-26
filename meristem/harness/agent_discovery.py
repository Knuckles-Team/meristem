"""Verified coding-agent discovery and the received agent plan (MERISTEM-SCM-R003.1).

The harness probes each configured agent through the sandbox gate runner,
sends the verified facts to the engine decision port and returns the solved
plan unchanged. It decides nothing about which agent runs.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from meristem.harness.changeset_loop import SandboxGateRunner
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineDecisionPort
from meristem.runtime.workspace import DevWorkspace

_VERSION = re.compile(r"\d+\.\d+\.\d+")
STALE = "DISCOVERY_RECEIPT_STALE"


@dataclass(frozen=True)
class VerifiedAgentFact:
    agent_id: str
    path: str
    revision: str
    exit_code: int
    probed_at_ms: int
    discovery_receipt_digest: str
    discovery_age_s: float
    capabilities: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SkillTransformation:
    skill: str
    agent_id: str
    artifact_digest: str


@dataclass(frozen=True)
class CodingAgentPlan:
    transformations: tuple[SkillTransformation, ...]
    facts: tuple[VerifiedAgentFact, ...]
    record_digest: str | None = None


def _receipt_digest(agent: str, path: str, revision: str, code: int, at_ms: int) -> str:
    body = {
        "agent": agent,
        "path": path,
        "revision": revision,
        "exit_code": code,
        "probed_at_ms": at_ms,
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def discover_agents(
    names: Sequence[str],
    runner: SandboxGateRunner,
    workspace: DevWorkspace,
    *,
    capabilities: Mapping[str, Any],
    clock_ms: Callable[[], int] = lambda: int(time.time() * 1000),
) -> tuple[VerifiedAgentFact, ...]:
    """One fact per agent whose ``<name> --version`` exits 0 with a version token."""
    facts: list[VerifiedAgentFact] = []
    for name in names:
        at_ms = clock_ms()
        observation = await runner.run_gate(workspace, f"{name} --version")
        match = _VERSION.search(observation.stdout)
        if observation.exit_code != 0 or match is None:
            continue
        revision = match.group(0)
        facts.append(
            VerifiedAgentFact(
                agent_id=name,
                path=name,
                revision=revision,
                exit_code=0,
                probed_at_ms=at_ms,
                discovery_receipt_digest=_receipt_digest(
                    name, name, revision, 0, at_ms
                ),
                discovery_age_s=(clock_ms() - at_ms) / 1000,
                capabilities=dict(capabilities.get(name, {})),
            )
        )
    return tuple(facts)


def _request(
    facts: Sequence[VerifiedAgentFact], skills: Sequence[str]
) -> dict[str, Any]:
    return {
        "kind": "coding_agents",
        "facts": [
            {
                "agent_id": f.agent_id,
                "path": f.path,
                "revision": f.revision,
                "exit_code": f.exit_code,
                "probed_at_ms": f.probed_at_ms,
                "discovery_receipt_digest": f.discovery_receipt_digest,
                "discovery_age_s": f.discovery_age_s,
                "capabilities": dict(f.capabilities),
            }
            for f in facts
        ],
        "skills": list(skills),
    }


async def plan_coding_agents(
    port: EngineDecisionPort | None,
    probe: Callable[[], Any],
    skills: Sequence[str],
    *,
    run_ref: str,
    emit: Callable[[Mapping[str, Any]], None],
) -> CodingAgentPlan:
    """Send the verified facts to the engine and return its solved plan unchanged.

    ``probe`` is awaited for the facts. A stale receipt re-probes once and
    then surfaces the refusal; with no port the step refuses
    ``engine_decision_unavailable``.
    """
    if port is None:
        raise ConsumerRefused("engine_decision_unavailable")
    for attempt in range(2):
        facts = await probe()
        answer = await port.assemble(_request(facts, skills), run_ref=run_ref)
        if answer.refusal_code == STALE and attempt == 0:
            continue
        if answer.refusal_code is not None:
            raise ConsumerRefused(answer.refusal_code)
        break
    emit(
        {
            "kind": "coding_agents_discovered",
            "run_ref": run_ref,
            "receipts": [f.discovery_receipt_digest for f in facts],
        }
    )
    raw = answer.payload.get("transformations", ())
    return CodingAgentPlan(
        transformations=tuple(
            SkillTransformation(
                str(t["skill"]), str(t["agent_id"]), str(t["artifact_digest"])
            )
            for t in raw
        ),
        facts=tuple(facts),
        record_digest=answer.record_digest,
    )
