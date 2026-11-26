"""Disclosed instruction sets, command config and tool necessity (R017.6, R018.1, R020.1, R045.1)."""

from __future__ import annotations

import hashlib

import pytest
from support.scripted_port import ScriptedPort, answer

from meristem.harness.command_file import _canonical_digest, apply_command_config
from meristem.harness.instructions import disclose_instructions, send_receipt
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.harness.tool_necessity import check_tool_necessity
from meristem.host_ports import EngineDecisionRefused

TEXT = "obey the rules\n"
HASH = hashlib.sha256(TEXT.encode()).hexdigest()


def _set(**over):
    return answer({"set_id": "s", "version": 3, "text": TEXT, "hash": HASH, **over})


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R017.6")
async def test_initial_then_runtime_disclosure_and_refusals():
    port = ScriptedPort(instruction_disclosure=_set())
    first = await disclose_instructions("r1", "initial", [], port, sequence=0)
    assert first.applied == f"s:3:{HASH}" and first.event["phase"] == "initial"
    assert port.calls[0][2] == {
        "phase": "initial",
        "run_id": "r1",
        "sequence": 0,
        "occurred_triggers": [],
    }
    later = await disclose_instructions(
        "r1", "runtime", ["policy_change"], port, sequence=1
    )
    assert later.event["trigger"] == ["policy_change"]
    with pytest.raises(ConsumerRefused) as raised:
        await disclose_instructions("r1", "initial", [], port, sequence=2, acted=True)
    assert raised.value.code == "disclosure_after_action"
    with pytest.raises(EngineDecisionRefused):
        await disclose_instructions("r1", "initial", [], None, sequence=0)


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R045.1")
async def test_receipt_binds_applied_version_and_hash():
    port = ScriptedPort(instruction_disclosure=[_set(), _set(hash="f" * 64)])
    got = await disclose_instructions("r1", "initial", [], port, sequence=0)
    assert got.text == TEXT and got.receipt == {"version": "3", "hash": HASH}
    await send_receipt(port, "r1", got)
    assert port.blobs == [got.receipt] and port.names("log") == ["DisclosureReceipt"]
    with pytest.raises(ConsumerRefused) as raised:
        await disclose_instructions("r1", "initial", [], port, sequence=0)
    assert raised.value.code == "disclosure_substituted"


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R018.1")
async def test_command_config_is_frozen_and_schema_checked():
    config = {"depth": 2}
    digest = _canonical_digest(config)
    good = answer({"config": config}, options=("mode:fast", f"config:{digest}"))
    port = ScriptedPort(
        command_config=[good, answer({"config": config}, options=("mode:fast",))]
    )
    args = {
        "mode_id": "fast",
        "expected_mode_revision": 1,
        "expected_config_digest": digest,
    }
    applied = await apply_command_config(port, run_ref="r1", **args)
    assert applied.config_digest == digest
    with pytest.raises(TypeError):
        applied.values["depth"] = 9  # type: ignore[index]
    with pytest.raises(ConsumerRefused) as raised:
        await apply_command_config(port, run_ref="r1", **args)
    assert raised.value.code == "command_config_schema_invalid"


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R020.1")
async def test_necessity_mismatch_is_advisory_and_a_refusal_changes_nothing():
    port = ScriptedPort(
        tool_necessity=[
            answer(
                {"necessity": "necessary", "agreement": False, "mismatch": "unneeded"}
            ),
            answer(refusal="TOOL_NECESSITY_UNKNOWN"),
        ]
    )
    args = {"run_id": "r1", "tool_id": "t", "model_id": "m", "model_called": True}
    span = await check_tool_necessity(port, model_call_id="c1", **args)
    assert span["agreement"] is False and span["mismatch"] == "unneeded"
    refused = await check_tool_necessity(port, model_call_id="c2", **args)
    assert refused == {"model_call_id": "c2", "refusal_code": "TOOL_NECESSITY_UNKNOWN"}
    with pytest.raises(EngineDecisionRefused):
        await check_tool_necessity(None, model_call_id="c3", **args)
