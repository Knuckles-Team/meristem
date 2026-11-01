"""Knowledge-graph readiness is no longer in Meristem."""

import pytest
from _census import assert_absent, references


@pytest.mark.spec("MERISTEM-RUN-R016")
def test_readiness_module_absent_from_au() -> None:
    assert_absent("knowledge_graph/readiness.py")
    assert references(("knowledge_graph.readiness",), include_tests=True) == []
