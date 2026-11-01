"""The SDK-duplicated base utilities module is deleted and nothing imports it."""

import pytest
from _census import assert_absent, references


@pytest.mark.spec("MERISTEM-RUN-R016")
def test_base_utilities_deleted() -> None:
    assert_absent("base_utilities.py")
    assert references(("base_utilities",), include_tests=True) == []


@pytest.mark.spec("MERISTEM-RUN-R016")
def test_no_repointed_importer_names_base_utilities() -> None:
    """Every importer row of R010 is satisfied: no file in the package names it,
    whether the file was repointed or deleted with its package."""
    assert references(("base_utilities",)) == []
