"""Reading from where the last successful run stopped (the runs of
`e5-exemple-incremental.md`)."""

from __future__ import annotations

from typing import Any

import psycopg
import pytest

from sql_query.incremental import Mark, Plan, last_mark, wrap
from sql_query.query import Query
from tests.conftest import Step
from tests.helpers import refused

QUERY = "SELECT id, email, updated_at FROM customers"


def add(db: psycopg.Connection[Any], *rows: tuple[int, str]) -> None:
    for id_, updated_at in rows:
        db.execute(
            "INSERT INTO customers (id, email, updated_at) VALUES (%s, %s, %s) "
            "ON CONFLICT (id) DO UPDATE SET updated_at = EXCLUDED.updated_at",
            (id_, f"c{id_}@ex.fr", f"2026-11-{updated_at}+00"),
        )


def ids(step: Step) -> list[int]:
    return [r["id"] for r in step.records]


@pytest.fixture
def incremental(step: Step, db: psycopg.Connection[Any]) -> Step:
    step.flow_file("query", "q.sql", QUERY)
    step.inputs.update(cursor="updated_at", key=["id"])
    return step


def test_the_first_run_reads_every_row_in_the_order_of_the_cursor(
    incremental: Step, db: psycopg.Connection[Any]
) -> None:
    add(db, (2, "19 10:00"), (1, "19 09:00"), (3, "19 10:00"))

    assert incremental.run() == 0

    assert ids(incremental) == [1, 2, 3]
    assert incremental.state == {
        "version": 1,
        "streams": {
            "records": {
                "cursor": "2026-11-19T10:00:00+00:00",
                "last_key": [3],
                "columns": {"cursor": "updated_at", "key": ["id"]},
            }
        },
    }
    assert incremental.schema["key_properties"] == ["id"]
    assert incremental.schema["bookmark_properties"] == ["updated_at"]


def test_the_next_run_reads_only_what_follows_and_the_key_parts_equal_cursors(
    incremental: Step, db: psycopg.Connection[Any]
) -> None:
    add(db, (1, "19 09:00"), (2, "19 10:00"), (3, "19 10:00"))
    assert incremental.run() == 0
    add(db, (4, "20 08:00"), (2, "20 08:10"), (5, "19 10:00"))

    assert incremental.next_run() == 0

    assert ids(incremental) == [5, 4, 2]
    assert incremental.state is not None
    assert incremental.state["streams"]["records"]["last_key"] == [2]


def test_a_run_with_nothing_new_keeps_the_mark(
    incremental: Step, db: psycopg.Connection[Any]
) -> None:
    add(db, (1, "19 09:00"))
    assert incremental.run() == 0
    first = incremental.state

    assert incremental.next_run() == 0

    assert ids(incremental) == []
    assert incremental.state == first


def test_start_limits_the_first_run(incremental: Step, db: psycopg.Connection[Any]) -> None:
    add(db, (1, "01 09:00"), (2, "19 09:00"))
    incremental.inputs["start"] = "2026-11-19"

    assert incremental.run() == 0

    assert ids(incremental) == [2]


def test_without_rows_nor_mark_no_state_is_written(incremental: Step) -> None:
    assert incremental.run() == 0

    assert incremental.state is None


def test_a_row_written_late_is_lost_without_lookback(
    incremental: Step, db: psycopg.Connection[Any]
) -> None:
    add(db, (3, "20 08:40"))
    assert incremental.run() == 0
    add(db, (1, "20 08:20"))

    assert incremental.next_run() == 0

    assert ids(incremental) == []


def test_lookback_reads_again_under_the_cursor_without_moving_it_back(
    incremental: Step, db: psycopg.Connection[Any]
) -> None:
    incremental.inputs["lookback"] = 1800
    add(db, (3, "20 08:40"))
    assert incremental.run() == 0
    add(db, (1, "20 08:20"))

    assert incremental.next_run() == 0

    assert ids(incremental) == [1, 3]
    assert incremental.state is not None
    assert incremental.state["streams"]["records"]["cursor"] == "2026-11-20T08:40:00+00:00"
    assert incremental.state["streams"]["records"]["last_key"] == [3]


def test_lookback_moves_the_mark_on_when_newer_rows_come(
    incremental: Step, db: psycopg.Connection[Any]
) -> None:
    incremental.inputs["lookback"] = 600
    add(db, (1, "20 08:00"))
    assert incremental.run() == 0
    add(db, (2, "20 09:00"))

    assert incremental.next_run() == 0

    assert ids(incremental) == [1, 2]
    assert incremental.state is not None
    assert incremental.state["streams"]["records"]["last_key"] == [2]


def test_a_row_without_cursor_is_rejected_at_each_run(
    incremental: Step, db: psycopg.Connection[Any]
) -> None:
    incremental.flow_file(
        "query",
        "q.sql",
        QUERY.replace("updated_at", "NULLIF(updated_at, '2026-11-19 09:00+00') AS updated_at"),
    )
    incremental.inputs["max_rejected"] = 5
    add(db, (1, "19 09:00"), (2, "19 10:00"))

    assert incremental.run() == 0
    assert ids(incremental) == [2]
    assert [(r["record"]["id"], r["reason"]) for r in incremental.rejected] == [
        (1, "cursor is null")
    ]

    assert incremental.next_run() == 0
    assert ids(incremental) == []
    assert [r["record"]["id"] for r in incremental.rejected] == [1]


def test_a_rejected_record_does_not_move_the_cursor(
    incremental: Step, db: psycopg.Connection[Any]
) -> None:
    incremental.flow_file("types", "t.yaml", "columns:\n  email: {type: string, maxLength: 5}\n")
    incremental.inputs["max_rejected"] = 1
    add(db, (1, "19 09:00"), (2, "19 10:00"))
    db.execute("UPDATE customers SET email = 'a@b' WHERE id = 1")

    assert incremental.run() == 0

    assert ids(incremental) == [1]
    assert incremental.state is not None
    assert incremental.state["streams"]["records"]["last_key"] == [1]


def test_a_key_of_two_columns(step: Step, db: psycopg.Connection[Any]) -> None:
    step.flow_file("query", "q.sql", "SELECT id, email, updated_at FROM customers")
    step.inputs.update(cursor="updated_at", key=["email", "id"])
    add(db, (1, "19 09:00"), (2, "19 09:00"))
    assert step.run() == 0
    add(db, (3, "19 09:00"))

    assert step.next_run() == 0

    assert ids(step) == [3]
    assert step.state is not None
    assert step.state["streams"]["records"]["last_key"] == ["c3@ex.fr", 3]


def test_an_integer_cursor(step: Step, db: psycopg.Connection[Any]) -> None:
    step.flow_file("query", "q.sql", "SELECT id, email FROM customers")
    step.inputs.update(cursor="id", key=["email"])
    add(db, (1, "19 09:00"), (2, "19 09:00"))
    assert step.run() == 0
    add(db, (3, "19 09:00"))

    assert step.next_run() == 0

    assert ids(step) == [3]
    assert step.state is not None
    assert step.state["streams"]["records"]["cursor"] == 3


def test_a_changed_key_asks_for_a_reset(
    incremental: Step, db: psycopg.Connection[Any], capsys: pytest.CaptureFixture[str]
) -> None:
    add(db, (1, "19 09:00"))
    assert incremental.run() == 0
    incremental.inputs["key"] = ["email"]

    assert incremental.next_run() == 1

    assert "reset it: cronplane state reset <flow> --step extract" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("inputs", "message"),
    [
        ({"cursor": "updated_at"}, "cursor needs key"),
        ({"cursor": "id", "key": ["id"]}, "key: id is the cursor already"),
        ({"cursor": "updated_at", "key": ["id", "id"]}, "key: a column is named twice"),
        ({"start": "2026-01-01"}, "start and lookback need cursor"),
        ({"cursor": "nope", "key": ["id"]}, "column q.nope does not exist"),
        (
            {"cursor": "email", "key": ["id"], "lookback": 60},
            "lookback needs a cursor of date and time",
        ),
        ({"cursor": "prefs", "key": ["id"]}, "cursor: prefs is of type jsonb"),
        ({"cursor": "updated_at", "key": ["nope"]}, "column q.nope does not exist"),
        ({"params": {"cronplane_x": "1"}}, "a name that starts with cronplane_ is the action's"),
    ],
)
def test_a_step_asked_wrong_says_why(
    step: Step,
    db: psycopg.Connection[Any],
    capsys: pytest.CaptureFixture[str],
    inputs: dict[str, Any],
    message: str,
) -> None:
    step.flow_file("query", "q.sql", "SELECT id, email, updated_at, prefs FROM customers")
    step.inputs.update(inputs)

    assert step.run() == 1

    assert message in capsys.readouterr().err


def test_a_state_without_its_last_key_asks_for_a_reset() -> None:
    plan = Plan(cursor="updated_at", key=("id",), start=None, lookback=0)
    kept: dict[str, Any] = {"cursor": "x", "last_key": "1", "columns": plan.columns}
    with refused("the state holds no last key for \\('id',\\); reset it"):
        last_mark(plan, kept, "extract")


def test_lookback_on_a_cursor_that_is_no_date_says_so() -> None:
    plan = Plan(cursor="code", key=("id",), start=None, lookback=60)
    query = Query.build("SELECT 1", {})
    for cursor in ["A-12", 12]:
        with refused("lookback needs a cursor of date and time: the state holds"):
            wrap(query, plan, Mark(cursor, [1]))
