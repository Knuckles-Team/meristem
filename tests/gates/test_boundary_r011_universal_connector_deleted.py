"""The universal database connector and its importers are gone from Meristem."""

import pytest
from _census import ROOT, assert_absent, references


@pytest.mark.spec("MERISTEM-RUN-R016")
def test_universal_connector_module_absent() -> None:
    assert_absent("protocols/universal_connector.py")
    assert references(("universal_connector",)) == []


@pytest.mark.spec("MERISTEM-RUN-R016")
def test_database_connector_and_db_tools_do_not_reference_it() -> None:
    assert_absent(
        "protocols/source_connectors/connectors/database.py", "tools/db_tools.py"
    )
    assert references(("universal_connector",)) == []


@pytest.mark.spec("MERISTEM-RUN-R016")
def test_tests_and_docs_do_not_reference_it() -> None:
    assert references(("universal_connector",), include_tests=True) == []
    assert not (ROOT / "docs").exists()
