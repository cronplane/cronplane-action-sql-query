"""What the tests of the parts share."""

import pytest

from sql_query.errors import SqlQueryError


def refused(match: str) -> pytest.RaisesExc[SqlQueryError]:
    """The step stops with an error that matches `match`."""
    return pytest.raises(SqlQueryError, match=match)
