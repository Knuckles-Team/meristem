"""Expand the chosen thought nodes (MERISTEM-HARNESS-CYCLE-R027.1)."""

from __future__ import annotations

import ports_support as support
import pytest
from support.recorded_engine import RecordedEnginePort

from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineDecisionRefused
from meristem.rlm import runner as rlm_runner

SPEC = "MERISTEM-HARNESS-CYCLE-R027.1"
# spec: MERISTEM-HARNESS-CYCLE-R027.1


def _arena() -> rlm_runner.ThoughtArena:
    return rlm_runner.ThoughtArena(
        nodes={
            "A": rlm_runner.ThoughtNode(
                "A",
                "think a",
                0,
                (
                    rlm_runner.ThoughtNode("A1", "a one", 1, (), 2),
                    rlm_runner.ThoughtNode("A2", "a two", 1, (), 2),
                ),
                3,
            ),
            "B": rlm_runner.ThoughtNode(
                "B",
                "think b",
                0,
                (rlm_runner.ThoughtNode("B1", "b one", 1, (), 2),),
                3,
            ),
            "C": rlm_runner.ThoughtNode("C", "think c", 0, (), 3),
        },
        frontier=("A", "B", "C"),
        beam_width=4,
    )


def _post_expansion(arena: rlm_runner.ThoughtArena) -> rlm_runner.ThoughtArena:
    """The arena state after A expands, for the second decision's params."""
    fresh = _arena()
    node = fresh.nodes["A"]
    fresh.tokens_spent = node.cost
    fresh.expanded = ("A",)
    fresh.nodes["A1"] = rlm_runner.ThoughtNode("A1", "a one", 1, (), 2)
    fresh.nodes["A2"] = rlm_runner.ThoughtNode("A2", "a two", 1, (), 2)
    fresh.frontier = ("B", "C", "A1", "A2")
    return fresh


def _decision_params(arena: rlm_runner.ThoughtArena) -> dict:
    return {
        "frontier": rlm_runner._frontier_summary(arena),
        "budget": rlm_runner._arena_budget(arena),
    }


async def test_expands_chosen_nodes_only():
    arena = _arena()
    support.register_engine_decision_port(
        RecordedEnginePort(
            {
                "source_test": __file__,
                "engine_commit": "test",
                "answers": [
                    {
                        "method": "thought_search",
                        "params": _decision_params(arena),
                        "answer": {
                            "outcome": "committed",
                            "payload": {"nodes": ["A"]},
                        },
                    },
                    {
                        "method": "thought_search",
                        "params": _decision_params(_post_expansion(arena)),
                        "answer": {
                            "outcome": "committed",
                            "payload": {
                                "nodes": ["A1", "A2"],
                                "stop": True,
                                "stop_reason": "budget",
                                "best_path": ["A", "A1"],
                            },
                        },
                    },
                ],
            }
        )
    )

    result = await rlm_runner.expand_thoughts(arena, support.PORTS)

    assert result.stop_reason == "budget"
    assert result.path == ("A", "A1")
    # Only the chosen nodes expanded: B and C never did.
    assert arena.expanded == ("A", "A1", "A2")
    assert "B" not in arena.expanded and "C" not in arena.expanded
    assert "B1" not in arena.nodes
    assert "A1" in arena.nodes and "A2" in arena.nodes
    assert arena.frontier == ("B", "C")


async def test_arena_over_budget_refuses_before_any_decision():
    arena = _arena()
    arena.beam_width = 1
    port = RecordedEnginePort({"answers": []})
    support.register_engine_decision_port(port)

    with pytest.raises(ConsumerRefused, match="arena_over_budget"):
        await rlm_runner.expand_thoughts(arena, support.PORTS)
    assert port.calls == []


async def test_stop_without_chosen_nodes_ends_the_search():
    arena = _arena()
    support.register_engine_decision_port(
        RecordedEnginePort(
            {
                "answers": [
                    {
                        "method": "thought_search",
                        "params": _decision_params(arena),
                        "answer": {
                            "outcome": "committed",
                            "payload": {"stop": True, "stop_reason": "done"},
                        },
                    }
                ],
            }
        )
    )

    result = await rlm_runner.expand_thoughts(arena, support.PORTS)

    assert (result.stop_reason, result.expanded, result.path) == ("done", (), ())


async def test_chosen_nodes_outside_the_frontier_refuse():
    arena = _arena()
    support.register_engine_decision_port(
        RecordedEnginePort(
            {
                "answers": [
                    {
                        "method": "thought_search",
                        "params": _decision_params(arena),
                        "answer": {
                            "outcome": "committed",
                            "payload": {"nodes": ["B1"]},
                        },
                    }
                ],
            }
        )
    )

    with pytest.raises(ConsumerRefused, match="thought_selection_invalid"):
        await rlm_runner.expand_thoughts(arena, support.PORTS)


async def test_unbound_port_refuses_and_decides_nothing():
    with pytest.raises(EngineDecisionRefused, match="engine_decision_unavailable"):
        await rlm_runner.expand_thoughts(_arena(), support.PORTS)


async def test_abstained_answer_decides_nothing():
    arena = _arena()
    support.register_engine_decision_port(
        RecordedEnginePort(
            {
                "answers": [
                    {
                        "method": "thought_search",
                        "params": _decision_params(arena),
                        "answer": {"outcome": "abstained"},
                    }
                ],
            }
        )
    )

    result = await rlm_runner.expand_thoughts(arena, support.PORTS)

    assert result.stop_reason == "abstained"
    assert (result.expanded, result.path) == ((), ())
    assert arena.frontier == ("A", "B", "C")
