"""Governance import is no longer in Meristem."""

import pytest
from _census import assert_absent, references


@pytest.mark.spec("MERISTEM-RUN-R016")
def test_governance_import_module_absent_from_au() -> None:
    assert_absent("knowledge_graph/governance_import.py")
    assert references(("governance_import",), include_tests=True) == []
