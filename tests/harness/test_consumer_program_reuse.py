"""Program reuse before the planning call (R022.4 to R022.8)."""

from __future__ import annotations

import json

import ports_support as support
import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from support.scripted_port import ScriptedPort, answer

from meristem.graph import HarnessConfig, RunPlan, create_graph_agent, execute_graph
from meristem.graph.state import ProgramReuseConfig, RunRequest
from meristem.harness import program_reuse as pr
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineDecisionRefused, ToolStepResult

CONFIG = ProgramReuseConfig(task_signature="sig", program_graph="graph")
MATCH = answer(
    {
        "steps": [{"server": "s", "tool": "t", "arguments": {"q": "x"}}],
        "match_kind": "exact",
        "score_micros": 990_000,
        "threshold_micros": 900_000,
    },
    options=("program_match:prog-1",),
    digest="look-1",
)


def _plan(model=None, **over) -> RunPlan:
    harness = HarnessConfig(consumers=("program_reuse",), program_reuse=CONFIG)
    return RunPlan(model=model or "m", steps=("task text",), harness=harness, **over)


def _request(caller=None) -> RunRequest:
    support.PORTS.caller = caller
    return support.request("task text", run_id="r1")


class _State:
    run_id = "r1"


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R022.4")
def test_config_and_match_parsing_refuse_invalid_input():
    steps, fields = pr.parse_program_match(MATCH)
    assert steps == (pr.BoundToolStep(0, "s", "t", {"q": "x"}),)
    assert fields["program_digest"] == "prog-1" and fields["score_micros"] == 990_000
    for payload in (
        {"steps": [{"server": "", "tool": "t"}]},
        {"steps": [{"server": "s", "tool": "t", "arguments": {"q": {"x": 1}}}]},
        {},
    ):
        with pytest.raises(ConsumerRefused) as refused:
            pr.parse_program_match(answer(payload, options=("program_match:p",)))
        assert refused.value.code == "program_match_invalid"


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R022.4")
async def test_empty_config_refuses():
    plan = _plan()
    bad = RunPlan(
        model="m",
        steps=("q",),
        harness=HarnessConfig(program_reuse=ProgramReuseConfig("", "g")),
    )
    with pytest.raises(ConsumerRefused) as refused:
        await pr.reuse_program(_request(), bad, _State(), ScriptedPort())
    assert refused.value.code == "program_reuse_config_invalid"
    with pytest.raises(EngineDecisionRefused):
        await pr.reuse_program(_request(), plan, _State(), None)


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R022.5")
async def test_hit_replaces_the_plan_with_one_answer_step_and_skips_are_honoured():
    calls: list[tuple] = []

    async def caller(server, tool, arguments, task_id):
        calls.append((server, tool, arguments, task_id))
        return ToolStepResult(result={"rows": 2})

    port = ScriptedPort(program_lookup=MATCH)
    plan, outcome = await pr.reuse_program(_request(caller), _plan(), _State(), port)
    assert outcome.outcome == "hit" and calls == [("s", "t", {"q": "x"}, "program:0")]
    assert plan.tool_names == () and plan.toolsets == ()
    assert json.loads(plan.steps[0]) == {"task": "task text", "results": [{"rows": 2}]}
    assert port.calls[0][2] == {
        "task_signature": "sig",
        "request_text": "task text",
        "program_graph": "graph",
    }
    for flags in ({"restricted": True}, {"restored": True}):
        _, skipped = await pr.reuse_program(
            _request(caller), _plan(), _State(), port, **flags
        )
        assert skipped.outcome == "skipped"
    assert len(port.calls) == 1


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R022.6")
@pytest.mark.parametrize(
    ("result", "code"),
    [
        (ToolStepResult(outcome="refused", error="no"), "program_step_refused"),
        (ToolStepResult(outcome="failed", error="bad"), "program_step_failed"),
        (RuntimeError("boom"), "program_step_failed"),
        (None, "tool_caller_unavailable"),
    ],
)
async def test_failures_fall_back_to_full_planning(result, code):
    async def caller(*_args):
        if isinstance(result, Exception):
            raise result
        return result

    port = ScriptedPort(program_lookup=MATCH)
    original = _plan()
    plan, outcome = await pr.reuse_program(
        _request(caller if result is not None else None), original, _State(), port
    )
    assert (outcome.outcome, outcome.refusal_code, outcome.failed_step) == (
        "fallback",
        code,
        0,
    )
    assert plan.steps == original.steps and plan.tool_names == original.tool_names


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R022.6")
async def test_abstention_keeps_the_plan():
    port = ScriptedPort(
        program_lookup=answer(
            outcome="abstained", refusal="PROGRAM_DEMOTED", digest="d"
        )
    )
    original = _plan()
    plan, outcome = await pr.reuse_program(_request(), original, _State(), port)
    assert plan is original and outcome.refusal_code == "PROGRAM_DEMOTED"


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R022.7")
async def test_each_outcome_is_traced_and_reported_once_without_failing_the_run():
    events: list[dict] = []
    port = ScriptedPort(RunRecord=answer(refusal="RUN_RECORD_REFUSED"))
    outcome = pr.ReuseOutcome("fallback", "sig", refusal_code="program_step_failed")
    code = await pr.report_reuse(outcome, _State(), port, events.append)
    assert code == "RUN_RECORD_REFUSED"
    assert [e["type"] for e in events] == [
        "program_reuse",
        "program_reuse_report_refused",
    ]
    assert port.blobs[0]["kind"] == "program_replay"
    skipped = await pr.report_reuse(
        pr.ReuseOutcome("skipped", "sig"), _State(), port, events.append
    )
    assert skipped is None and len(port.blobs) == 1


def _counting_model(counter: list[int]) -> FunctionModel:
    def respond(messages, info):
        counter.append(len(info.function_tools))
        return ModelResponse(parts=[TextPart("answer")])

    return FunctionModel(respond)


async def _run(port, caller, counter):
    support.register_run_decision_provider(
        lambda request: _plan(_counting_model(counter))
    )
    support.register_engine_decision_port(port)
    support.register_tool_caller(caller)
    graph, config = create_graph_agent(agent_model="m", ports=support.PORTS)
    events: list[dict] = []
    support.register_run_event_sink(events.append)
    result = await execute_graph(
        graph, config, "task text", run_id="r1", ports=support.PORTS
    )
    return result, events


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R022.8")
async def test_served_answers_hit_and_demote_through_the_run_loop():
    async def caller(*_args):
        return ToolStepResult(result="rows")

    hit_counter: list[int] = []
    hit, events = await _run(ScriptedPort(program_lookup=MATCH), caller, hit_counter)
    assert hit["status"] == "completed" and len(hit_counter) == 1
    reuse = next(e for e in events if e.get("type") == "program_reuse")
    assert reuse["outcome"] == "hit"

    support.reset()
    demoted = answer(outcome="abstained", refusal="PROGRAM_DEMOTED", digest="d")
    counter: list[int] = []
    result, events = await _run(ScriptedPort(program_lookup=demoted), caller, counter)
    assert result["status"] == "completed"
    reuse = next(e for e in events if e.get("type") == "program_reuse")
    assert (reuse["outcome"], reuse["refusal_code"]) == ("abstain", "PROGRAM_DEMOTED")
