"""The default consumer steps driven through the real run loop
(MERISTEM-HARNESS-CYCLE-R016.3, R034.1, R034.2, R036.1, R036.2, R072)."""

from __future__ import annotations

import contextlib
import hashlib
import json

import ports_support as support
import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from support.recorded_engine import RecordedEnginePort

from meristem import host_ports
from meristem.graph import HarnessConfig, RunPlan, RunStatus, run
from meristem.harness import search_state as ss
from meristem.harness.replay import step_digest

# spec: MERISTEM-HARNESS-CYCLE-R016.3
# spec: MERISTEM-HARNESS-CYCLE-R034.1
# spec: MERISTEM-HARNESS-CYCLE-R034.2
# spec: MERISTEM-HARNESS-CYCLE-R036.1
# spec: MERISTEM-HARNESS-CYCLE-R036.2
# spec: MERISTEM-HARNESS-CYCLE-R072


def _plan(consumers, prompts=None):
    def respond(messages, info):
        if prompts is not None:
            prompts.append(messages[-1].parts[-1].content)
        return ModelResponse(parts=[TextPart("reply")])

    return RunPlan(
        model=FunctionModel(respond),
        steps=("question",),
        harness=HarnessConfig(consumers=tuple(consumers)),
    )


def _events():
    events: list[dict] = []
    support.register_run_event_sink(events.append)
    return events


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R036.1")
@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R036.2")
async def test_fact_prose_reaches_the_model_with_exact_byte_count():
    params = {"node_ids": ["n1"], "token_budget": 50}
    fact = {"fact_id": "f1", "subject": "Ü", "predicate": "owns", "object": "x"}
    support.register_engine_decision_port(
        RecordedEnginePort(
            {
                "answers": [
                    {
                        "method": "ContextFacts",
                        "params": params,
                        "answer": {
                            "outcome": "solved",
                            "payload": {"facts": [fact], "omitted_count": 2},
                        },
                    }
                ]
            }
        )
    )
    events = _events()
    prompts: list = []
    result = await run(
        support.request(task="t", metadata={"context_facts": params}),
        plan=_plan(["context_facts"], prompts),
    )
    assert result.status is RunStatus.COMPLETED
    assert prompts == ["Ü owns x. [f1]\n\nquestion"]
    (event,) = [e for e in events if e.get("type") == "context_compiled"]
    assert event["fact_ids"] == ["f1"] and event["omitted_count"] == 2
    assert event["byte_count"] == len("Ü owns x. [f1]".encode())


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R036.1")
async def test_unrenderable_fact_fails_the_run_without_a_model_call():
    params = {"node_ids": ["n1"], "token_budget": 50}
    bad = {"fact_id": "f1", "subject": "", "predicate": "p", "object": "o"}
    support.register_engine_decision_port(
        RecordedEnginePort(
            {
                "answers": [
                    {
                        "method": "ContextFacts",
                        "params": params,
                        "answer": {"outcome": "solved", "payload": {"facts": [bad]}},
                    }
                ]
            }
        )
    )
    prompts: list = []
    result = await run(
        support.request(task="t", metadata={"context_facts": params}),
        plan=_plan(["context_facts"], prompts),
    )
    assert result.status is RunStatus.FAILED and prompts == []
    assert "fact_unrenderable" in (result.error or "")


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R016.3")
async def test_critique_pages_read_after_the_step():
    base = {"root_node_id": "n", "snapshot_id": "s", "limit": 1}

    def entry(cursor, nxt):
        return {
            "method": "TraceCritique",
            "params": {"query": {**base, "cursor": cursor}},
            "answer": {"outcome": "solved", "payload": {"next_cursor": nxt}},
        }

    support.register_engine_decision_port(
        RecordedEnginePort({"answers": [entry(None, "c1"), entry("c1", None)]})
    )
    events = _events()
    result = await run(
        support.request(task="t", metadata={"trace_critique": base}),
        plan=_plan(["trace_critique"]),
    )
    assert result.status is RunStatus.COMPLETED
    (event,) = [e for e in events if e.get("type") == "critique_read"]
    assert event["cursors"] == [None, "c1"]


class SnapshotEngine:
    """Keeps the one submitted snapshot and serves it back with its digests."""

    def __init__(self, *, corrupt=False):
        self.writes: list[dict] = []
        self.reads: list[dict] = []
        self.content = ""
        self.corrupt = corrupt

    async def write(self, method, params, *, run_ref):
        assert method == "SyncMemorySnapshot"
        self.writes.append(params)
        self.content = params["request"]["action"]["content"]
        return host_ports.EngineAnswer(
            "solved", payload={"outcome": "accepted", "head": {"revision": 1}}
        )

    async def read(self, method, params, *, run_ref):
        assert method == "ReadMemorySnapshot"
        self.reads.append(params)
        document = json.loads(self.content)
        evidence = {
            f"{name}_digest": ss.field_digest(document[name]) for name in ss.FIELDS
        }
        if self.corrupt:
            evidence["evidence_digest"] = "bad"
        return host_ports.EngineAnswer(
            "solved", payload={"content": self.content, "search_state": evidence}
        )


class HostCheckpoint:
    admission_identity = "admitted/run"

    def __init__(self):
        self.content = None

    async def restore(self):
        return self.content

    async def save(self, content):
        self.content = content


def _bind_checkpoint(port):
    @contextlib.asynccontextmanager
    async def scope(request, plan):
        yield port

    support.register_run_checkpoint_factory(scope)


SEARCH = {
    "search_run_id": "sr",
    "doc_id": "d1",
    "evidence": ["e1"],
    "constraints": [],
    "open_claims": ["o1"],
}


def _search_request():
    return support.request(task="t", run_id="same", metadata={"search_state": SEARCH})


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R034.1")
@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R034.2")
async def test_search_state_saved_then_restored_before_the_checkpoint():
    engine = SnapshotEngine()
    support.register_engine_decision_port(engine)
    checkpoint = HostCheckpoint()
    _bind_checkpoint(checkpoint)
    events = _events()
    first = await run(_search_request(), plan=_plan(["search_state"]))
    assert first.status is RunStatus.COMPLETED
    (write,) = engine.writes
    action = write["request"]["action"]
    assert action["action"] == "submit" and action["expected_revision"] is None
    assert (
        action["content_digest"]
        == hashlib.sha256(action["content"].encode()).hexdigest()
    )
    assert json.loads(action["content"])["transcript"] == ["reply"]
    assert [
        e["state_revision"] for e in events if e["type"] == "search_state_saved"
    ] == [1]
    # A restart restores the four fields from the engine before the checkpoint.
    again = await run(_search_request(), plan=_plan(["search_state"]))
    assert again.status is RunStatus.COMPLETED and len(engine.reads) == 1


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R034.2")
async def test_mismatched_digest_refuses_the_restore():
    engine = SnapshotEngine()
    support.register_engine_decision_port(engine)
    _bind_checkpoint(HostCheckpoint())
    await run(_search_request(), plan=_plan(["search_state"]))
    engine.corrupt = True
    prompts: list = []
    again = await run(_search_request(), plan=_plan(["search_state"], prompts))
    assert again.status is RunStatus.FAILED and prompts == []
    assert "search_state_incomplete" in (again.error or "")


@pytest.mark.spec("MERISTEM-HARNESS-CYCLE-R072")
async def test_first_divergence_fails_the_replay_at_that_index():
    steps = ({"i": 0}, {"i": 1}, {"i": 2})
    stored = [step_digest(steps[0]), step_digest({"i": 9}), step_digest(steps[2])]
    events = _events()
    result = await run(
        support.request(
            task="t", replay_events=steps, metadata={"stored_step_digests": stored}
        ),
        plan=_plan(["replay_digests"]),
    )
    assert result.status is RunStatus.FAILED
    assert [
        e["first_divergence"] for e in events if e["type"] == "replay_compared"
    ] == [1]
    stored[1] = step_digest(steps[1])
    ok = await run(
        support.request(
            task="t", replay_events=steps, metadata={"stored_step_digests": stored}
        ),
        plan=_plan(["replay_digests"]),
    )
    assert ok.status is RunStatus.COMPLETED
