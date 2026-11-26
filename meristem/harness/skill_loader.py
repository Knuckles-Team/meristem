"""Three-stage skill loading with a load-time script (R094, R096, R100).

Stage 1 loads the name and description of each skill in the received load
set. Stage 2 loads the body when a skill is selected. Stage 3 loads one
reference file per explicit request. The budgets are received values with
fallback defaults. The loader reads skill content through a received source
and decides nothing.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

STAGE_OUT_OF_ORDER = "SKILL_STAGE_OUT_OF_ORDER"
LOAD_SCRIPT_FAILED = "SKILL_LOAD_SCRIPT_FAILED"
PLACEHOLDER = "{{load_script_output}}"
LOAD_SCRIPT_MAX_BYTES = 8192
LOAD_SCRIPT_TIMEOUT_SECONDS = 30.0


class SkillSource(Protocol):
    """Reads skill content; the host binds it to the served skill resources."""

    async def metadata(self, skill_ref: str) -> tuple[str, str]:
        """Return ``(name, description)``."""
        ...

    async def body(self, skill_ref: str) -> str: ...

    async def reference(self, skill_ref: str, path: str) -> str: ...

    async def load_script(self, skill_ref: str) -> str | None:
        """Return the declared ``load_script`` text, or ``None``."""
        ...


class ScriptWorkspace(Protocol):
    """The part of a workspace backend the loader runs a script in."""

    async def exec_shell(
        self, script: str, env: dict[str, str], timeout: float
    ) -> tuple[int, str, str]: ...


class SkillStageOutOfOrder(RuntimeError):
    """A stage 3 request came before stage 2."""

    code = STAGE_OUT_OF_ORDER


@dataclass(frozen=True, slots=True)
class LoadBudgets:
    """Received stage budgets; the defaults are fallbacks only."""

    stage1_max_tokens: int = 100
    stage2_max_lines: int = 500


@dataclass(frozen=True, slots=True)
class SkillLoadEvent:
    skill_ref: str
    stage: int
    token_count: int


@dataclass(frozen=True, slots=True)
class LoadedSkill:
    skill_ref: str
    text: str
    truncated: bool = False


def count_tokens(text: str) -> int:
    """A stable token estimate: one token per four characters, rounded up."""
    return (len(text) + 3) // 4


def _clip_tokens(text: str, limit: int) -> str:
    return text[: limit * 4] if count_tokens(text) > limit else text


@dataclass
class SkillLoader:
    """Loads skills in stages and emits one :class:`SkillLoadEvent` per load.

    ``hidden`` skills (an ablation arm ``Without``) are absent from stage 1.
    ``removed_parts`` maps a skill ref to body sections or reference paths
    that an ablation arm ``WithoutPart`` removes.
    """

    source: SkillSource
    emit: Callable[[SkillLoadEvent], None]
    budgets: LoadBudgets = field(default_factory=LoadBudgets)
    workspace: ScriptWorkspace | None = None
    trace: Callable[[str, Mapping[str, Any]], None] = lambda kind, payload: None
    hidden: frozenset[str] = frozenset()
    removed_parts: Mapping[str, frozenset[str]] = field(default_factory=dict)
    _stage2: set[str] = field(default_factory=set)

    async def stage1(self, load_set: Sequence[str]) -> list[LoadedSkill]:
        """Name and description of each skill in the load set."""
        loaded: list[LoadedSkill] = []
        for ref in load_set:
            if ref in self.hidden:
                continue
            name, description = await self.source.metadata(ref)
            text = _clip_tokens(
                f"{name}: {description}", self.budgets.stage1_max_tokens
            )
            self.emit(SkillLoadEvent(ref, 1, count_tokens(text)))
            loaded.append(LoadedSkill(ref, text))
        return loaded

    async def stage2(self, skill_ref: str) -> LoadedSkill:
        """The body, cut at the line budget, with the load script output."""
        body = self._without_parts(skill_ref, await self.source.body(skill_ref))
        lines = body.split("\n")
        truncated = len(lines) > self.budgets.stage2_max_lines
        if truncated:
            body = "\n".join(lines[: self.budgets.stage2_max_lines])
        if PLACEHOLDER in body:
            body = await self._run_load_script(skill_ref, body)
        self._stage2.add(skill_ref)
        self.emit(SkillLoadEvent(skill_ref, 2, count_tokens(body)))
        return LoadedSkill(skill_ref, body, truncated)

    async def stage3(self, skill_ref: str, path: str) -> LoadedSkill:
        """One reference file; refused before stage 2 of the same skill."""
        if skill_ref not in self._stage2:
            raise SkillStageOutOfOrder(STAGE_OUT_OF_ORDER)
        if path in self.removed_parts.get(skill_ref, frozenset()):
            return LoadedSkill(skill_ref, "")
        text = await self.source.reference(skill_ref, path)
        self.emit(SkillLoadEvent(skill_ref, 3, count_tokens(text)))
        return LoadedSkill(skill_ref, text)

    def _without_parts(self, skill_ref: str, body: str) -> str:
        parts = self.removed_parts.get(skill_ref, frozenset())
        if not parts:
            return body
        kept: list[str] = []
        skipping = False
        for line in body.split("\n"):
            if line.startswith("#"):
                skipping = line.lstrip("# ").strip() in parts
            if not skipping:
                kept.append(line)
        return "\n".join(kept)

    async def _run_load_script(self, skill_ref: str, body: str) -> str:
        script = await self.source.load_script(skill_ref)
        if script is None or self.workspace is None:
            return body
        code, out, _err = await self.workspace.exec_shell(
            script, {}, LOAD_SCRIPT_TIMEOUT_SECONDS
        )
        if code != 0:
            self.trace(LOAD_SCRIPT_FAILED, {"skill_ref": skill_ref, "exit_code": code})
            return body
        clipped = out.encode()[:LOAD_SCRIPT_MAX_BYTES].decode(errors="ignore")
        return body.replace(PLACEHOLDER, clipped)


__all__ = [
    "LOAD_SCRIPT_FAILED",
    "PLACEHOLDER",
    "STAGE_OUT_OF_ORDER",
    "LoadBudgets",
    "LoadedSkill",
    "SkillLoadEvent",
    "SkillLoader",
    "SkillSource",
    "SkillStageOutOfOrder",
    "count_tokens",
]
