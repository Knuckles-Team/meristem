"""The tool guard asks the host to admit a risky action; any refusal denies."""

from __future__ import annotations

import pytest

from meristem.harness.tool_action_guard import ToolActionRefused, admit_tool_action
from meristem.host_ports import ConfirmVerdict

SPEC = "MERISTEM-HARNESS-CYCLE-R055"
ARGS = {"run_ref": "r1", "tool": "send", "arguments_digest": "d" * 64}


def confirm_with(verdict):
    asked = []

    async def confirm(kind, payload, run_ref):
        asked.append((kind, dict(payload), run_ref))
        if isinstance(verdict, Exception):
            raise verdict
        return verdict

    return confirm, asked


@pytest.mark.spec(SPEC)
async def test_refusal_denies_and_admit_runs_tool():
    confirm, asked = confirm_with(ConfirmVerdict("admit", ref="appr-1"))
    admission = await admit_tool_action(
        confirm, tool_class="egress", after_untrusted=True, **ARGS
    )
    assert (admission.approval_ref, admission.decision_ref) == ("appr-1", "appr-1")
    assert asked[0][0] == "tool_action"
    assert asked[0][1]["confirmation_kind"] == "tool_action"
    for verdict in (
        ConfirmVerdict("deny"),
        ConfirmVerdict("admit", refusal_code="ToolActionRefusal"),
        RuntimeError("no response"),
    ):
        confirm, _ = confirm_with(verdict)
        with pytest.raises(ToolActionRefused):
            await admit_tool_action(
                confirm, tool_class="delegation", after_untrusted=True, **ARGS
            )
    with pytest.raises(ToolActionRefused):
        await admit_tool_action(
            None, tool_class="privileged_call", after_untrusted=True, **ARGS
        )


@pytest.mark.spec(SPEC)
@pytest.mark.parametrize(
    ("tool_class", "untrusted"), [("read", True), ("egress", False)]
)
async def test_unguarded_actions_ask_nothing(tool_class, untrusted):
    confirm, asked = confirm_with(ConfirmVerdict("deny"))
    result = await admit_tool_action(
        confirm, tool_class=tool_class, after_untrusted=untrusted, **ARGS
    )
    assert result is None and asked == []
