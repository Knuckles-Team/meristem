"""Admit and validate parallel sub-calls (MERISTEM-HARNESS-CYCLE-R017.5)."""

from __future__ import annotations

import ports_support as support
import pytest
from support.recorded_engine import RecordedEnginePort

from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineDecisionRefused
from meristem.rlm import runner as rlm_runner

SPEC = "MERISTEM-HARNESS-CYCLE-R017.5"
# spec: MERISTEM-HARNESS-CYCLE-R017.5


def _request(parent_ref: str = "parent") -> rlm_runner.SubCallRequest:
    return rlm_runner.SubCallRequest(
        signature_digest="sig-digest",
        model_digest="model-digest",
        tool_digest="tool-digest",
        parent_ref=parent_ref,
    )


def _params(call: rlm_runner.SubCallRequest) -> dict:
    return {
        "signature_digest": call.signature_digest,
        "model_digest": call.model_digest,
        "tool_digest": call.tool_digest,
        "parent_ref": call.parent_ref,
    }


async def test_matching_and_mismatching_output():
    call = _request()
    support.register_engine_decision_port(
        RecordedEnginePort(
            {
                "source_test": __file__,
                "engine_commit": "test",
                "answers": [
                    {
                        "method": "sub_call_admission",
                        "params": _params(call),
                        "answer": {
                            "outcome": "committed",
                            "record_digest": "admission-1",
                        },
                    }
                ],
            }
        )
    )

    admission = await rlm_runner.admit_sub_call(call, support.PORTS)
    assert admission.admitted is True
    assert admission.record_digest == "admission-1"

    signature = {"signature_id": "s1", "output_schema": "str"}
    assert rlm_runner.validate_sub_output(signature, "ok") is True
    with pytest.raises(ConsumerRefused, match="sub_call_output_mismatch"):
        rlm_runner.validate_sub_output(signature, 42)
    # A bool is not an int for a signed int schema.
    int_signature = {"signature_id": "s2", "output_schema": "int"}
    assert rlm_runner.validate_sub_output(int_signature, 42) is True
    with pytest.raises(ConsumerRefused, match="sub_call_output_mismatch"):
        rlm_runner.validate_sub_output(int_signature, True)


async def test_not_admitted_when_the_engine_abstains():
    call = _request()
    support.register_engine_decision_port(
        RecordedEnginePort(
            {
                "answers": [
                    {
                        "method": "sub_call_admission",
                        "params": _params(call),
                        "answer": {"outcome": "abstained"},
                    }
                ],
            }
        )
    )

    admission = await rlm_runner.admit_sub_call(call, support.PORTS)

    assert admission.admitted is False
    assert admission.record_digest is None


async def test_engine_refusal_passes_through():
    call = _request()
    support.register_engine_decision_port(
        RecordedEnginePort(
            {
                "answers": [
                    {
                        "method": "sub_call_admission",
                        "params": _params(call),
                        "answer": {
                            "outcome": "abstained",
                            "refusal_code": "SUBCALL_TOOL_NOT_ADMITTED",
                        },
                    }
                ],
            }
        )
    )

    with pytest.raises(ConsumerRefused, match="SUBCALL_TOOL_NOT_ADMITTED"):
        await rlm_runner.admit_sub_call(call, support.PORTS)


async def test_unbound_port_refuses_and_the_call_does_not_start():
    with pytest.raises(EngineDecisionRefused, match="engine_decision_unavailable"):
        await rlm_runner.admit_sub_call(_request(), support.PORTS)


async def test_missing_digest_refuses():
    support.register_engine_decision_port(RecordedEnginePort({"answers": []}))
    call = _request(parent_ref="")

    with pytest.raises(ConsumerRefused, match="sub_call_request_incomplete"):
        await rlm_runner.admit_sub_call(call, support.PORTS)


async def test_dict_output_schema_matches_keys():
    signature = {
        "signature_id": "s3",
        "output_schema": {"type": "dict", "keys": ["a", "b"]},
    }
    assert rlm_runner.validate_sub_output(signature, {"a": 1, "b": 2}) is True
    with pytest.raises(ConsumerRefused, match="sub_call_output_mismatch"):
        rlm_runner.validate_sub_output(signature, {"a": 1})
    with pytest.raises(ConsumerRefused, match="sub_call_output_mismatch"):
        rlm_runner.validate_sub_output(signature, ["a", "b"])


async def test_signature_without_schema_refuses():
    with pytest.raises(ConsumerRefused, match="sub_call_signature_invalid"):
        rlm_runner.validate_sub_output({"signature_id": "s4"}, "ok")
    with pytest.raises(ConsumerRefused, match="sub_call_signature_invalid"):
        rlm_runner.validate_sub_output(
            {"signature_id": "s5", "output_schema": "no-such-type"}, "ok"
        )
