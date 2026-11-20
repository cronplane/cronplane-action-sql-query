"""The files of the flow a set gives the step."""

from __future__ import annotations

from pathlib import Path

from sql_query.flow_files import one_file
from tests.helpers import refused


def test_a_set_gives_one_file(tmp_path: Path) -> None:
    (tmp_path / "query/sql").mkdir(parents=True)
    (tmp_path / "query/sql/q.sql").write_text("SELECT 1")
    env = {"CRONPLANE_FLOW_FILES": str(tmp_path)}
    assert one_file(env, "query", (".sql",)) == tmp_path / "query/sql/q.sql"
    assert one_file(env, "types", (".yaml",)) is None
    assert one_file({}, "query", (".sql",)) is None


def test_a_set_of_two_files_is_refused(tmp_path: Path) -> None:
    (tmp_path / "query").mkdir()
    (tmp_path / "query/a.sql").write_text("SELECT 1")
    (tmp_path / "query/b.sql").write_text("SELECT 2")
    with refused(r"one .sql file is expected \(given: a.sql, b.sql\)"):
        one_file({"CRONPLANE_FLOW_FILES": str(tmp_path)}, "query", (".sql",))
