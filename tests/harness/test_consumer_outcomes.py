"""Record run outcomes through ``DecisionLog`` ``Evaluate``.

Bound to MERISTEM-HARNESS-CYCLE-R004.1: at run end the harness sends one
``Evaluate`` per stored record digest through the engine decision port, maps
the terminal status to the success value and never changes the run result.
"""

from __future__ import annotations

import ports_support as support
import pytest
import recorded_engine as recorded
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from meristem import host_ports
from meristem.graph import RunPlan, StopRule, create_graph_agent, execute_graph
from meristem.graph.state import GraphState
from meristem.harness import outcomes as consumer
from meristem.harness.run_lifecycle import RunLifecycle


def _answer(record_id, evaluation_id, success, *, refusal=None, digest="ev"):
    return {
        "method": "log",
        "params": {
            "op": "Evaluate",
            "record_id": record_id,
            "evaluation_id": evaluation_id,
            "success": success,
        },
        "answer": {
            "outcome": "committed",
            "refusal_code": refusal,
            "record_digest": digest,
        },
    }


def _port(answers) -> recorded.RecordedEnginePort:
    return recorded.RecordedEnginePort(
        {
            "source_test": (
                "meristem:tests/harness/test_consumer_outcomes.py"
                "::test_run_outcome_recorded_via_evaluate"
            ),
            "engine_commit": "a" * 64,
            "answers": answers,
        }
    )


def _model(text="done") -> FunctionModel:
    def respond(messages, info):
        return ModelResponse(parts=[TextPart(text)])

    return FunctionModel(respond)


def _note_records(*records):
    def recorder(state, plan):
        for kind, digest in records:
            state.note_record(kind, digest)

    return recorder


def _lifecycle(*records) -> RunLifecycle:
    lifecycle = RunLifecycle(consumers=("outcomes", "records"))

    async def report(_state):
        await consumer.report_outcomes(_state, support.PORTS)

    lifecycle.add("records", "on_start", _note_records(*records))
    lifecycle.add("outcomes", "on_end", report)
    return lifecycle


def _plan(**over) -> RunPlan:
    return RunPlan(model=_model(), steps=("q",), **over)


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R004.1")
async def test_run_outcome_recorded_via_evaluate():
    graph, config = create_graph_agent(agent_model="m", ports=support.PORTS)
    events: list[dict] = []
    support.register_run_event_sink(lambda event: events.append(dict(event)))

    # Run 1 (completed): one Evaluate per stored record digest, success True;
    # a refused evaluation passes its engine code through and keeps the
    # completed result.
    port = _port(
        [
            _answer("dig-a", "run-ok:context_budget", True, refusal="EVAL_REJECTED"),
            _answer("dig-b", "run-ok:route", True, digest="ev-b"),
        ]
    )
    support.register_engine_decision_port(port)
    support.register_run_decision_provider(
        lambda request: _plan(
            lifecycle=_lifecycle(("context_budget", "dig-a"), ("route", "dig-b"))
        )
    )
    result = await execute_graph(
        graph, config, "q", run_id="run-ok", ports=support.PORTS
    )
    assert result["status"] == "completed"
    assert port.calls == [
        (
            "log",
            recorded.params_digest(
                {
                    "op": "Evaluate",
                    "record_id": "dig-a",
                    "evaluation_id": "run-ok:context_budget",
                    "success": True,
                }
            ),
        ),
        (
            "log",
            recorded.params_digest(
                {
                    "op": "Evaluate",
                    "record_id": "dig-b",
                    "evaluation_id": "run-ok:route",
                    "success": True,
                }
            ),
        ),
    ]
    evaluated = [e for e in events if e["type"] == "run_outcome_evaluated"]
    assert [e["evaluation_id"] for e in evaluated] == [
        "run-ok:context_budget",
        "run-ok:route",
    ]
    assert [e["success"] for e in evaluated] == [True, True]
    assert [e["refusal_code"] for e in evaluated] == ["EVAL_REJECTED", None]
    assert [e["record_digest"] for e in evaluated] == ["ev", "ev-b"]

    # Run 2 (failed): the stored decision evaluates with success False.
    port = _port([_answer("dig-c", "run-fail:step_verification", False)])
    support.register_engine_decision_port(port)
    events.clear()

    def boom(messages, info):
        raise OSError("model down")

    support.register_run_decision_provider(
        lambda request: _plan(
            model=FunctionModel(boom),
            lifecycle=_lifecycle(("step_verification", "dig-c")),
        )
    )
    result = await execute_graph(
        graph, config, "q", run_id="run-fail", ports=support.PORTS
    )
    assert result["status"] == "failed"
    assert port.calls == [
        (
            "log",
            recorded.params_digest(
                {
                    "op": "Evaluate",
                    "record_id": "dig-c",
                    "evaluation_id": "run-fail:step_verification",
                    "success": False,
                }
            ),
        )
    ]
    assert [e["success"] for e in events if e["type"] == "run_outcome_evaluated"] == [
        False
    ]

    # Run 3 (stopped): a censored evaluation carries success None.
    port = _port([_answer("dig-d", "run-stop:route", None)])
    support.register_engine_decision_port(port)
    support.register_run_decision_provider(
        lambda request: _plan(
            model=_model("STOP"),
            stop=StopRule(stop_markers=("STOP",)),
            lifecycle=_lifecycle(("route", "dig-d")),
        )
    )
    result = await execute_graph(
        graph, config, "q", run_id="run-stop", ports=support.PORTS
    )
    assert result["status"] == "stopped"
    assert port.calls == [
        (
            "log",
            recorded.params_digest(
                {
                    "op": "Evaluate",
                    "record_id": "dig-d",
                    "evaluation_id": "run-stop:route",
                    "success": None,
                }
            ),
        )
    ]
    assert [e["success"] for e in events if e["type"] == "run_outcome_evaluated"] == [
        None
    ]

    # A repeat note of the same kind stores one digest, so one Evaluate.
    state = GraphState(run_id="run-repeat", query="q", status="completed")
    state.note_record("route", "dig-b")
    state.note_record("route", "dig-b")
    port = _port([_answer("dig-b", "run-repeat:route", True)])
    support.register_engine_decision_port(port)
    await consumer.report_outcomes(state, support.PORTS)
    assert len(port.calls) == 1

    # Without a bound port the step refuses and decides nothing.
    with pytest.raises(host_ports.EngineDecisionRefused) as refused:
        await consumer.report_outcomes(state, host_ports.HostPorts())
    assert refused.value.code == "engine_decision_unavailable"

    # The success mapping is the closed set of the row: a refused evaluation
    # never changes the run result, the status mapping never does either.
    assert consumer.SUCCESS_BY_STATUS == {
        "completed": True,
        "failed": False,
        "stopped": None,
    }
    assert consumer.evaluation_id("run-ok", "route") == "run-ok:route"
