"""Order the sealed skill trees for the try, without re-ranking
(MERISTEM-HARNESS-CYCLE-R021.1)."""

from __future__ import annotations

import pytest

from meristem.harness import plan_runtime
from meristem.harness.run_lifecycle import ConsumerRefused
from meristem.host_ports import EngineAnswer

SPEC = "MERISTEM-HARNESS-CYCLE-R021.1"
# spec: MERISTEM-HARNESS-CYCLE-R021.1


def _answer(trees: dict) -> EngineAnswer:
    return EngineAnswer(outcome="solved", payload={"skill_trees": trees})


@pytest.mark.spec(SPEC)
def test_skills_tried_in_rank_order():
    trees = {
        "t1": [
            {"skill_id": "c", "rank": 2},
            {"skill_id": "a", "rank": 1},
            {"skill_id": "b", "rank": 3},
        ]
    }

    (order,) = plan_runtime.order_skills(_answer(trees))

    # Tried in rank order; the harness keeps the sealed ranks, never re-ranks.
    assert [skill.skill_id for skill in order.skills] == ["a", "c", "b"]
    assert [skill.rank for skill in order.skills] == [1, 2, 3]
    assert [skill.attempt for skill in order.skills] == [0, 1, 2]


@pytest.mark.spec(SPEC)
def test_parent_tried_before_children():
    trees = {
        "t1": [
            {"skill_id": "leaf", "rank": 1, "parent_skill_id": "root"},
            {"skill_id": "root", "rank": 2},
        ]
    }

    (order,) = plan_runtime.order_skills(_answer(trees))

    # A parent the rank puts after a child moves ahead of that child.
    assert [skill.skill_id for skill in order.skills] == ["root", "leaf"]
    assert [skill.rank for skill in order.skills] == [2, 1]
    assert order.task_id == "t1"


@pytest.mark.spec(SPEC)
def test_cycle_refuses_skill_tree_cycle():
    trees = {
        "t1": [
            {"skill_id": "a", "rank": 1, "parent_skill_id": "b"},
            {"skill_id": "b", "rank": 2, "parent_skill_id": "a"},
        ]
    }

    with pytest.raises(ConsumerRefused, match="skill_tree_cycle"):
        plan_runtime.order_skills(_answer(trees))


@pytest.mark.spec(SPEC)
def test_answer_without_trees_orders_nothing():
    answer = EngineAnswer(outcome="solved", payload={})

    assert plan_runtime.order_skills(answer) == ()
