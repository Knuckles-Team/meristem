"""Every consumer step reached through ``execute_graph`` with fake host ports."""

from __future__ import annotations

import dataclasses
import hashlib

import ports_support as support
import pytest
from support.journey import bind_caller, execute, text_plan, tool_plan
from support.recorded_engine import RecordedEnginePort

from meristem.harness import default_consumers
from meristem.host_ports import (
    ConfirmVerdict,
    EngineAnswer,
    SummaryProviderDisabled,
    SummaryResult,
)


def engine(*answers):
    port = RecordedEnginePort({"answers": list(answers)})
    support.register_engine_decision_port(port)
    return port


def answer(method, params, outcome="solved", **fields):
    return {
        "method": method,
        "params": params,
        "answer": {"outcome": outcome, **fields},
    }


def only(events, kind):
    (event,) = [e for e in events if e.get("type") == kind]
    return event


TOOLS = [("read_doc", {}), ("send_mail", {"to": "x"})]
CLASSES = {"send_mail": "egress"}


def guarded_plan():
    return tool_plan(
        ["tool_action_guard", "run_trace"],
        TOOLS,
        action_space={"tool_classes": CLASSES},
    )


def bind_confirm(verdict):
    asked = []

    async def confirm(kind, payload, run_ref):
        asked.append((kind, dict(payload)))
        if isinstance(verdict, Exception):
            raise verdict
        return verdict

    support.PORTS.confirm = confirm
    return asked


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R055")
async def test_refusal_denies_and_admit_runs_tool():
    traces = []

    async def sink(trace):
        traces.append(trace)

    support.PORTS.run_trace = sink
    called = bind_caller()
    asked = bind_confirm(ConfirmVerdict("admit", ref="appr-1"))
    result = await execute(guarded_plan())
    assert result["status"] == "completed" and called == ["read_doc", "send_mail"]
    assert [kind for kind, _ in asked] == ["tool_action"]
    send = [s for s in traces[0]["steps"] if s["name"] == "send_mail"][0]
    assert (send["approval_ref"], send["decision_ref"]) == ("appr-1", "appr-1")
    for verdict in (
        ConfirmVerdict("deny"),
        ConfirmVerdict("admit", refusal_code="ToolActionRefusal"),
        RuntimeError("no response"),
        None,
    ):
        called = bind_caller()
        if verdict is None:
            support.PORTS.confirm = None
        else:
            bind_confirm(verdict)
        refused = await execute(guarded_plan())
        assert refused["status"] == "failed" and called == ["read_doc"]


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R055")
async def test_a_risky_tool_before_untrusted_content_asks_nothing():
    called = bind_caller()
    asked = bind_confirm(ConfirmVerdict("deny"))
    plan = tool_plan(
        ["tool_action_guard"],
        [("send_mail", {})],
        action_space={"tool_classes": CLASSES},
    )
    assert (await execute(plan))["status"] == "completed"
    assert asked == [] and called == ["send_mail"]


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R056")
async def test_refused_item_is_not_applied(events):
    meta = {"held_item": {"item_ref": "item-1", "item_kind": "program"}}
    plan = text_plan(["held_item"])
    asked = bind_confirm(ConfirmVerdict("admitted", ref="v-1"))
    result = await execute(plan, meta)
    assert result["output"] == "reply" and asked[0][1]["item_ref"] == "item-1"
    assert only(events, "held_item_decided")["verdict_ref"] == "v-1"
    bind_confirm(ConfirmVerdict("rejected", ref="v-2"))
    held = await execute(plan, meta)
    assert held["status"] == "completed" and held["output"] == ""
    for verdict in (
        ConfirmVerdict("admitted", refusal_code="HeldItemRefusal"),
        RuntimeError("down"),
    ):
        bind_confirm(verdict)
        refused = await execute(plan, meta)
        assert refused["status"] == "failed" and refused["output"] == ""
    support.PORTS.confirm = None
    unbound = await execute(plan, meta)
    assert "HELD_ITEM_CONFIRM_UNAVAILABLE" in unbound["error"]


PINS = {"skill": "s1", "model": "m1", "dataset": "d1", "criteria": "c1"}


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R057")
async def test_receipt_submitted_and_attestation_applied(events):
    cert = {"request": {"tenant_id": "t"}, "pins": PINS, "criterion_ids": ["k1"]}
    body = {
        "op": "certify_local_skill",
        "request": {"tenant_id": "t", "pins": PINS, "criterion_ids": ["k1"]},
    }
    port = engine(
        answer("DecisionEval", body, "committed", payload={"attestation_id": "att-9"})
    )
    plan = text_plan(["certification"])
    result = await execute(plan, {"certification": cert})
    assert result["status"] == "completed"
    assert only(events, "certification_attested")["attestation_id"] == "att-9"
    assert [call[0] for call in port.calls] == ["DecisionEval"]
    bad = {**cert, "pins": {**PINS, "dataset": ""}}
    refused = await execute(plan, {"certification": bad})
    assert "CERTIFICATION_PIN_MISSING" in refused["error"]


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R061")
async def test_turn_end_records_monotonic_elapsed_with_receipt_ref(events, monkeypatch):
    ticks = iter([5_000_000, 12_000_000])
    monkeypatch.setattr(default_consumers, "CLOCK", lambda: next(ticks))
    port = engine(
        {
            "method": "log",
            "params": {"op": "Evaluate"},
            "answer": {"outcome": "committed"},
        }
    )
    result = await execute(
        text_plan(["turn_timing"]), {"turn_timing": {"receipt_ref": "rcpt-1"}}
    )
    assert result["status"] == "completed"
    ((op, body),) = port.bodies
    assert op == "Evaluate" and body["receipt_ref"] == "rcpt-1"
    assert body["clock"] == "client_monotonic"
    assert body["client_request_elapsed_ms"] == 7
    assert body["client_run_elapsed_ms"] >= 0
    assert only(events, "turn_timing_recorded")["recorded"] is True


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R061")
async def test_clock_failure_omits_the_observation(events, monkeypatch):
    def broken():
        raise OSError("no clock")

    monkeypatch.setattr(default_consumers, "CLOCK", broken)
    port = engine()
    result = await execute(
        text_plan(["turn_timing"]), {"turn_timing": {"receipt_ref": "rcpt-1"}}
    )
    assert result["status"] == "completed" and port.bodies == []
    assert only(events, "turn_timing_recorded")["recorded"] is False


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R062")
async def test_five_techniques_emit_bound_receipts(events):
    port = engine(
        *[
            {
                "method": "log",
                "params": {"op": "Evaluate"},
                "answer": {"outcome": "committed"},
            }
            for _ in range(5)
        ]
    )
    measured = []

    async def measure(technique, arm, config_digest):
        measured.append((technique, arm, config_digest))
        return {"tokens_per_s": 10 if arm == "baseline" else 14}

    support.PORTS.technique_measure = measure
    bench = {"model_id": "m1", "config_digest": "cfg-1"}
    result = await execute(
        text_plan(["technique_benchmark"]), {"technique_benchmark": bench}
    )
    assert result["status"] == "completed"
    assert [body["technique"] for _, body in port.bodies] == [
        "paged_cache",
        "prefix_reuse",
        "fused_kernels",
        "quantized_cache",
        "expert_dispatch",
    ]
    assert {c for _, _, c in measured} == {"cfg-1"} and len(measured) == 10
    first = port.bodies[0][1]
    assert first["baseline"] != first["candidate"] and first["model_id"] == "m1"
    assert len(only(events, "technique_receipts_logged")["techniques"]) == 5
    support.PORTS.technique_measure = None
    refused = await execute(
        text_plan(["technique_benchmark"]), {"technique_benchmark": bench}
    )
    assert "TECHNIQUE_MEASURE_UNAVAILABLE" in refused["error"]


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R065")
async def test_step_order_follows_received_risk(events):
    query = "PROPAGATE x RISK HISTORY"
    engine(
        answer(
            "UnifiedQuery",
            {"uql": query},
            payload={"ordering": ["c", "a"], "unknown": ["b"]},
        )
    )
    risk = {"query": query, "steps": ["a", "b", "c", "d"]}
    result = await execute(text_plan(["risk_order"]), {"risk_order": risk})
    assert result["status"] == "completed"
    applied = only(events, "risk_order_applied")
    assert applied["ordered"] == ["c", "a"] and applied["review"] == ["b", "d"]


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R066")
async def test_hypotheses_stay_proposals(events):
    params = {"graph": "g", "arity": 3}
    hypothesis = {"subject": "s", "evidence_class": "inferred"}
    port = engine(
        answer(
            "GraphLearnPredictNary",
            params,
            payload={"hypotheses": [hypothesis], "graph_revision": 4},
        )
    )
    result = await execute(text_plan(["nary_proposals"]), {"nary_proposals": params})
    assert result["status"] == "completed"
    assert only(events, "nary_proposals_read")["count"] == 1
    assert [call[0] for call in port.calls] == ["GraphLearnPredictNary"]
    assert port.bodies == []


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R067")
async def test_retrieval_step_renders_context_compile_bundle():
    request = {"budget_tokens": 4096}
    items = [{"text": "second chosen first", "citation": "doc-9"}, {"text": "then"}]
    engine(answer("ContextCompile", {"request": request}, payload={"items": items}))
    prompts: list = []
    plan = text_plan(["context_compile"], prompts)
    result = await execute(plan, {"context_compile": request})
    assert result["status"] == "completed"
    assert prompts == ["[1] second chosen first (doc-9)\n[2] then\n\nquestion"]
    engine(
        answer(
            "ContextCompile", {"request": request}, "abstained", refusal_code="WINDOW"
        )
    )
    prompts.clear()
    await execute(plan, {"context_compile": request})
    assert prompts == ["WINDOW\n\nquestion"]


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R068")
async def test_one_invocation_one_receipt(events):
    calls = []

    async def provider(context, admission_ref):
        calls.append(admission_ref)
        return SummaryResult("the summary", "prov", "model-1", "rev-3")

    support.PORTS.summary_provider = provider
    summary = {
        "context": {"records": ["a"]},
        "admission_ref": "adm-1",
        "owner_requirement_id": "ARBORA-OPS-R065",
    }
    plan = text_plan(["summary_receipt"])
    result = await execute(plan, {"summary_receipt": summary})
    assert result["status"] == "completed" and calls == ["adm-1"]
    receipt = only(events, "summary_receipt_minted")["receipt"]
    assert receipt["output_digest"] == hashlib.sha256(b"the summary").hexdigest()
    assert len(receipt["receipt_digest"]) == 64

    async def disabled(context, admission_ref):
        raise SummaryProviderDisabled

    support.PORTS.summary_provider = disabled
    refused = await execute(plan, {"summary_receipt": summary})
    assert "SUMMARY_PROVIDER_DISABLED" in refused["error"]
    support.PORTS.summary_provider = None
    refused = await execute(plan, {"summary_receipt": summary})
    assert "SUMMARY_PROVIDER_UNAVAILABLE" in refused["error"]


class WriteOnlyEngine(RecordedEnginePort):
    """An engine whose one served write answers as given; it records the write."""

    def __init__(self, **answer_fields):
        super().__init__({"answers": []})
        self.fields = answer_fields
        self.sent: list[tuple[str, dict]] = []

    async def write(self, method, params, *, run_ref):
        self.sent.append((method, dict(params)))
        return EngineAnswer(**self.fields)


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R073")
async def test_manifest_binds_spans_and_sources(events):
    bind_caller()
    walk = {"sections": [{"path": "a.py", "source_revision": "rev1"}]}
    plan = tool_plan(["walkthrough"], [("open", {"path": "a.py"})])
    accepting = WriteOnlyEngine(outcome="committed", record_digest="m-1")
    support.register_engine_decision_port(accepting)
    result = await execute(plan, {"walkthrough": walk})
    assert result["status"] == "completed"
    ((method, body),) = accepting.sent
    assert method == "AgentComponent" and body["op"] == "add_node"
    (section,) = body["node"]["sections"]
    assert section["spans"] and section["spans"][0]["step"] >= 0
    assert only(events, "walkthrough_sent")["record_digest"] == "m-1"
    support.register_engine_decision_port(
        WriteOnlyEngine(outcome="abstained", refusal_code="SOURCE_REVISION_STALE")
    )
    refused = await execute(plan, {"walkthrough": walk})
    assert "SOURCE_REVISION_STALE" in refused["error"]


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R075")
@pytest.mark.parametrize(
    ("receipt", "status", "code"),
    [
        ({"verdict": "matched"}, "completed", None),
        (
            {"verdict": "mismatched", "code": "READBACK_DIGEST"},
            "failed",
            "READBACK_DIGEST",
        ),
        ({"verdict": "absent"}, "failed", "READBACK_MISMATCHED"),
    ],
)
async def test_success_only_after_matched_readback(receipt, status, code):
    request = {"batch": "b1"}
    engine(answer("VerifyIngestReadback", request, payload=receipt))
    result = await execute(text_plan(["ingest_readback"]), {"ingest_readback": request})
    assert result["status"] == status
    if code:
        assert code in result["error"]


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R016.2")
async def test_run_trace_reaches_the_port_as_plain_data():
    traces = []

    async def sink(trace):
        traces.append(trace)

    support.PORTS.run_trace = sink
    bind_caller()
    plan = tool_plan(["run_trace"], [("read_doc", {"a": 1}), ("list", {})])
    result = await execute(plan)
    assert result["status"] == "completed"
    (trace,) = traces
    assert trace["run_id"] == "r1"
    first, second = trace["steps"]
    assert (first["name"], second["name"]) == ("read_doc", "list")
    assert first["outcome"] == "ok" and first["elapsed_seconds"] >= 0
    assert (
        len(first["args_digest"]) == 64
        and first["args_digest"] != second["args_digest"]
    )
    support.PORTS.run_trace = None
    refused = await execute(plan)
    assert "run_trace_unavailable" in refused["error"]


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R075")
@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R056")
async def test_a_finish_gate_refusal_on_a_dependency_plan_ends_failed_with_the_code():
    called = bind_caller()
    request = {"batch": "b1"}
    engine(
        answer(
            "VerifyIngestReadback",
            request,
            payload={"verdict": "mismatched", "code": "READBACK_DIGEST"},
        )
    )
    plan = tool_plan(["ingest_readback"], [("ingest", {}), ("finish", {})])
    result = await execute(plan, {"ingest_readback": request})
    assert called == ["ingest", "finish"]
    assert result["status"] == "failed" and "READBACK_DIGEST" in result["error"]
    bind_confirm(ConfirmVerdict("rejected", ref="v-2"))
    held = {"held_item": {"item_ref": "i", "item_kind": "program"}}
    kept = await execute(tool_plan(["held_item"], [("a", {})]), held)
    assert kept["status"] == "completed" and not kept["error"]
    bind_confirm(RuntimeError("down"))
    refused = await execute(tool_plan(["held_item"], [("a", {})]), held)
    assert (
        refused["status"] == "failed" and "HELD_ITEM_CONFIRM_FAILED" in refused["error"]
    )


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R073")
@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R070")
async def test_the_output_call_event_precedes_the_gate_and_the_run_state(events):
    """The output task's event reports the call; the run state follows the gate."""
    bind_caller()
    seen = []

    async def gate_probe(current):
        seen.append(
            [e["type"] for e in events if e.get("task_id") == "t0"]
            + [current.status.value]
        )

    from meristem.harness.run_lifecycle import RunLifecycle

    lifecycle = RunLifecycle(consumers=("probe",))
    lifecycle.add("probe", "before_finish", gate_probe)
    plan = dataclasses.replace(
        tool_plan(["probe"], [("open", {"path": "a.py"})]), lifecycle=lifecycle
    )
    assert (await execute(plan))["status"] == "completed"
    assert seen == [["task_started", "task_completed", "running"]]
