"""The one rule read of the run loop.

The run reads the guardrail rules once through one engine decision and
uses only the rule versions whose state is ``Active``; a proposed,
superseded or revoked version is never read.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineDecisionPort, EngineDecisionRefused


@dataclass(frozen=True, slots=True)
class ActiveRules:
    """The active rule versions one guardrail entailment admits.

    ``rules`` are the ``(rule_id, version)`` pairs the run trace
    records; ``record_digest`` links the trace to the decision record.
    """

    rules: tuple[tuple[str, str], ...]
    record_digest: str | None


async def active_rule_versions(
    request: Any, port: EngineDecisionPort | None, *, run_ref: str
) -> ActiveRules:
    """Read only the active rule version through one decision.

    One ``guardrail_entailment`` decision carries the run's scope. The
    harness uses only rules whose ``RuleVersion`` state is ``Active``
    and never reads a ``Proposed``, ``Superseded`` or ``Revoked``
    version; an answer that lists a version in another state refuses
    ``rule_version_not_active``. Each rule id and version enters the
    run trace. Engine codes pass through; with no bound port it
    refuses ``engine_decision_unavailable`` and the guarded action
    does not start.
    """
    if port is None:
        raise EngineDecisionRefused("engine_decision_unavailable")
    metadata = getattr(request, "metadata", None) or {}
    scope = metadata.get("scope") if isinstance(metadata, Mapping) else None
    if type(scope) is not str or not scope:
        scope = "run"
    answer = await port.decide(
        "guardrail_entailment", {"scope": scope}, run_ref=run_ref
    )
    if answer.refusal_code is not None:
        raise ConsumerRefused(answer.refusal_code)
    rules_raw = (
        answer.payload.get("rules")
        if answer.outcome in ("solved", "committed")
        else None
    )
    if not isinstance(rules_raw, (list, tuple)):
        return ActiveRules(rules=(), record_digest=answer.record_digest)
    rules: list[tuple[str, str]] = []
    for rule in rules_raw:
        if not isinstance(rule, Mapping):
            raise ConsumerRefused("rule_version_not_active")
        if rule.get("state") != "Active":
            raise ConsumerRefused("rule_version_not_active")
        rule_id = rule.get("rule_id")
        version = rule.get("version")
        if type(rule_id) is not str or not rule_id:
            raise ValueError("rule versions need rule ids")
        if type(version) is not str and type(version) is not int:
            raise ValueError("rule versions need a version")
        rules.append((rule_id, str(version)))
    return ActiveRules(rules=tuple(rules), record_digest=answer.record_digest)


__all__ = ["ActiveRules", "active_rule_versions"]
