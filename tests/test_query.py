"""The query of the flow and the values of its parameters."""

from __future__ import annotations

from pathlib import Path

import pytest

from sql_query.query import Query, read_source
from tests.helpers import refused


def test_the_query_and_params_name_the_same_parameters() -> None:
    query = Query.build("SELECT 1 WHERE a = :a AND b = :b", {"a": "1", "b": "2"})
    assert query.params == {"a": "1", "b": "2"}


@pytest.mark.parametrize("params", [{}, {"a": "1", "c": "3"}])
def test_a_gap_between_query_and_params_is_refused(params: dict[str, str]) -> None:
    with refused(r"the query uses the parameters \[a\], params gives"):
        Query.build("SELECT 1 WHERE a = :a", params)


def test_a_colon_in_a_literal_reads_as_a_parameter_until_escaped() -> None:
    with refused(r"A colon in a literal of the query is written \\:"):
        Query.build("""SELECT '{"a":1}'::jsonb""", {})
    assert Query.build("""SELECT '{"a"\\:1}'::jsonb""", {}).params == {}


def test_an_empty_query_is_refused() -> None:
    with refused("the query file is empty"):
        Query.build("  \n", {})


def test_the_query_file_is_utf8(tmp_path: Path) -> None:
    (tmp_path / "q.sql").write_bytes(b"\xff")
    with refused("not UTF-8 text"):
        read_source(tmp_path / "q.sql")


def test_a_missing_query_file_is_an_error(tmp_path: Path) -> None:
    with refused("cannot be read"):
        read_source(tmp_path / "q.sql")
