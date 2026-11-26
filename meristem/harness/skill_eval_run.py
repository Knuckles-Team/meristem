"""Replay one skill eval case in a clean room, with ablation arms (R098-R100).

The harness runs a received case and reports the trajectory and the output
digest as two separate fields. It scores nothing: the engine scores. Each
case starts a fresh workspace and records an environment attestation.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Literal, Protocol

from meristem.harness.skill_events import send_engine
from meristem.harness.skill_loader import SkillLoader

MOCK_MISSING = "MOCK_MISSING"
LAPTOP_DEPENDENCY = "LAPTOP_DEPENDENCY"
RESULT_METHOD = "DecisionEval"
RESULT_OP = "skill_eval"

_SYSTEM_PATHS = ("/dev/null", "/dev/stdout", "/dev/stderr", "/usr/", "/bin/", "/lib")


def digest(value: Any) -> str:
    """Stable SHA-256 of a JSON-shaped value."""
    blob = json.dumps(value, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()


@dataclass(frozen=True, slots=True)
class SkillEvalCase:
    case_id: str
    kind: Literal["golden", "real"]
    prompt: str
    tool_mocks: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AblationArm:
    """``Without`` hides the skill; ``WithoutPart`` removes one named part."""

    kind: Literal["Without", "WithoutPart"]
    skill_ref: str
    part: str | None = None


@dataclass(frozen=True, slots=True)
class EnvironmentAttestation:
    sandbox_digest: str
    env_digest: str
    host_paths_read: int


@dataclass(frozen=True, slots=True)
class EvalRunResult:
    """One case result. No field names the ablation arm."""

    case_id: str
    blind_id: str
    actual_trajectory: tuple[tuple[str, str], ...]
    output_digest: str | None
    attestation: EnvironmentAttestation
    error_code: str | None = None


class EvalRefused(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class EvalWorkspace(Protocol):
    name: str
    workdir: str

    async def start(self) -> None: ...

    async def exec_shell(
        self, script: str, env: dict[str, str], timeout: float
    ) -> tuple[int, str, str]: ...

    async def stop(self) -> None: ...


class EvalRouter:
    """What the replayed run calls: tools, reads and commands."""

    def __init__(
        self,
        case: SkillEvalCase,
        workspace: EvalWorkspace,
        live: Callable[[str, Mapping[str, Any]], Awaitable[Any]] | None = None,
    ) -> None:
        self._case = case
        self._ws = workspace
        self._live = live
        self.trajectory: list[tuple[str, str]] = []
        self.host_paths_read = 0

    def use_skill(self, name: str) -> None:
        self.trajectory.append(("skill", name))

    async def call_tool(self, name: str, args: Mapping[str, Any]) -> Any:
        self.trajectory.append(("tool", name))
        if self._case.kind == "golden":
            key = digest({"name": name, "args": args})
            if key not in self._case.tool_mocks:
                raise EvalRefused(MOCK_MISSING)
            return self._case.tool_mocks[key]
        if self._live is None:
            raise EvalRefused(MOCK_MISSING)
        return await self._live(name, args)

    def read_path(self, path: str) -> None:
        self._inside(path)

    async def run_command(self, command: str) -> tuple[int, str, str]:
        for token in re.findall(r"(?<![\w.])/[\w./-]+", command):
            self._inside(token)
        return await self._ws.exec_shell(command, {}, 30.0)

    def _inside(self, path: str) -> None:
        if path.startswith(_SYSTEM_PATHS):
            return
        root = PurePosixPath(self._ws.workdir)
        target = PurePosixPath(path)
        if target.is_absolute() and root not in (target, *target.parents):
            self.host_paths_read += 1
            raise EvalRefused(LAPTOP_DEPENDENCY)
        if ".." in target.parts:
            self.host_paths_read += 1
            raise EvalRefused(LAPTOP_DEPENDENCY)


#: ``(case, router, loader_options, seed) -> final output``.
Driver = Callable[[SkillEvalCase, EvalRouter, dict[str, Any], int], Awaitable[Any]]
Submit = Callable[[str, Mapping[str, Any]], Awaitable[Any]]


def clean_room_workspace() -> EvalWorkspace:
    """A fresh docker workspace: empty environment, no network, one temp root."""
    from meristem.runtime.docker_workspace import DockerWorkspace

    return DockerWorkspace(network="none")  # type: ignore[return-value]


def blind_id(case_id: str, arm: AblationArm | None, seed: int) -> str:
    """A label that does not reveal the arm, stable for a seed."""
    label = "none" if arm is None else f"{arm.kind}:{arm.skill_ref}:{arm.part}"
    return hashlib.sha256(f"{seed}|{case_id}|{label}".encode()).hexdigest()[:16]


def loader_options(arm: AblationArm | None) -> dict[str, Any]:
    """The loader settings that apply the arm."""
    if arm is None:
        return {}
    if arm.kind == "Without":
        return {"hidden": frozenset({arm.skill_ref})}
    return {"removed_parts": {arm.skill_ref: frozenset({arm.part or ""})}}


def apply_arm(loader: SkillLoader, arm: AblationArm | None) -> SkillLoader:
    """Return ``loader`` with the arm applied."""
    for name, value in loader_options(arm).items():
        setattr(loader, name, value)
    return loader


async def run_eval_case(
    case: SkillEvalCase,
    *,
    new_workspace: Callable[[], EvalWorkspace],
    drive: Driver,
    submit: Submit,
    arm: AblationArm | None = None,
    seed: int = 0,
    live: Callable[[str, Mapping[str, Any]], Awaitable[Any]] | None = None,
    envelope: Mapping[str, Any] | None = None,
) -> EvalRunResult:
    """Run one case in a fresh empty workspace and send the result.

    ``envelope`` is the received ``SkillEvalRequest`` without its runs
    (``skill_ref``, ``tenant_id``, ``cases``, ``known_tools`` and an optional
    ``calibration``). The run goes to the engine as ``DecisionEval`` op
    ``skill_eval``; a pinned engine without it makes the send unavailable and
    the result is still returned.
    """
    workspace = new_workspace()
    await workspace.start()
    router = EvalRouter(case, workspace, live)
    output: Any = None
    code: str | None = None
    try:
        output = await drive(case, router, loader_options(arm), seed)
    except EvalRefused as refusal:
        code = refusal.code
    finally:
        await workspace.stop()
    attestation = EnvironmentAttestation(
        sandbox_digest=digest(
            {"backend": workspace.name, "workdir": workspace.workdir}
        ),
        env_digest=digest({}),
        host_paths_read=router.host_paths_read,
    )
    result = EvalRunResult(
        case_id=case.case_id,
        blind_id=blind_id(case.case_id, arm, seed),
        actual_trajectory=tuple(router.trajectory),
        output_digest=None if code else digest(output),
        attestation=attestation,
        error_code=code,
    )
    run: dict[str, Any] = {
        "case_id": result.case_id,
        "actual_digest": result.output_digest or digest({"error": code}),
        "actual_trajectory": [
            {"kind": kind, "name": name} for kind, name in result.actual_trajectory
        ],
        "attestation": {
            "sandbox_digest": attestation.sandbox_digest,
            "env_digest": attestation.env_digest,
            "host_paths_read": attestation.host_paths_read,
        },
    }
    request = {**(envelope or {}), "runs": [run]}
    await send_engine(submit, RESULT_METHOD, {"op": RESULT_OP, "request": request})
    return result


__all__ = [
    "LAPTOP_DEPENDENCY",
    "RESULT_METHOD",
    "RESULT_OP",
    "MOCK_MISSING",
    "AblationArm",
    "EnvironmentAttestation",
    "EvalRefused",
    "EvalRouter",
    "EvalRunResult",
    "SkillEvalCase",
    "apply_arm",
    "clean_room_workspace",
    "blind_id",
    "digest",
    "loader_options",
    "run_eval_case",
]
