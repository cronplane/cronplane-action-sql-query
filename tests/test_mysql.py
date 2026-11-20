"""The action on MySQL and MariaDB, those of the kit's fixtures
(`SQL_QUERY_MYSQL_SERVERS`: the servers to test, `mysql,mariadb` by default)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pymysql
import pytest

from tests.conftest import Step

SERVERS = {"mysql": 53306, "mariadb": 53307}
LONG_JOIN = (
    "SELECT COUNT(*) AS n FROM information_schema.COLLATIONS a, information_schema.COLLATIONS b, "
    "information_schema.COLLATIONS c, information_schema.COLLATIONS d"
)
CHOSEN = os.environ.get("SQL_QUERY_MYSQL_SERVERS", "mysql,mariadb").split(",")

TABLE = """CREATE TABLE items (
  id BIGINT PRIMARY KEY, n TINYINT, ok BOOLEAN, price DECIMAL(10,2), ratio DOUBLE,
  label VARCHAR(20), note TEXT, kind ENUM('a','b'), tags SET('x','y'), day DATE,
  at DATETIME NOT NULL, spent TIME, born YEAR, extra JSON)"""
ROW = """INSERT INTO items VALUES (1, 7, TRUE, 12.50, 0.5, 'first', 'long text', 'a', 'x,y',
  '2026-11-20', '2026-11-20 08:00:00', '-01:30:00', 2026, '{"k": 1}')"""


@pytest.fixture(params=CHOSEN)
def server(request: pytest.FixtureRequest) -> Iterator[tuple[str, pymysql.Connection[Any]]]:
    name: str = request.param
    connection = pymysql.connect(
        host="127.0.0.1", port=SERVERS[name], user="fixture",
        password="fixture",  # noqa: S106 # the kit's fixtures
        database="fixture", autocommit=True,
    )  # fmt: skip
    with connection.cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS items")
        cursor.execute(TABLE)
    yield name, connection
    connection.close()


@pytest.fixture
def my_step(step: Step, server: tuple[str, pymysql.Connection[Any]]) -> Step:
    port = SERVERS[server[0]]
    step.url = f"mysql+pymysql://fixture:fixture@127.0.0.1:{port}/fixture?charset=utf8mb4"
    step.allow = f"127.0.0.1:{port}"
    return step


def run_sql(server: tuple[str, pymysql.Connection[Any]], sql: str) -> None:
    with server[1].cursor() as cursor:
        cursor.execute(sql)


def test_each_mysql_type_gets_a_json_type(
    my_step: Step, server: tuple[str, pymysql.Connection[Any]]
) -> None:
    run_sql(server, ROW)
    my_step.flow_file("query", "q.sql", "SELECT * FROM items")

    assert my_step.run() == 0

    mysql = server[0] == "mysql"
    properties = my_step.schema["schema"]["properties"]
    assert properties["ok"] == {"type": ["boolean", "null"]}
    assert properties["price"] == {"type": ["number", "null"]}
    assert properties["at"] == {"type": ["string", "null"]}
    # MariaDB's JSON is a LONGTEXT: text, unless the types file says otherwise.
    assert properties["extra"] == ({} if mysql else {"type": ["string", "null"]})
    assert my_step.records == [
        {"id": 1, "n": 7, "ok": True, "price": 12.5, "ratio": 0.5, "label": "first",
         "note": "long text", "kind": "a", "tags": "x,y", "day": "2026-11-20",
         "at": "2026-11-20T08:00:00", "spent": "-01:30:00", "born": 2026,
         "extra": {"k": 1} if mysql else '{"k": 1}'},
    ]  # fmt: skip


def test_a_binary_column_is_a_clear_error(
    my_step: Step, capsys: pytest.CaptureFixture[str]
) -> None:
    my_step.flow_file("query", "q.sql", "SELECT CAST('a' AS BINARY(2)) AS raw")

    assert my_step.run() == 1

    error = capsys.readouterr().err
    assert "column raw: the MySQL type varbinary has no JSON type" in error
    assert "CAST(raw AS CHAR) AS raw" in error


def test_the_transaction_is_read_only(my_step: Step, capsys: pytest.CaptureFixture[str]) -> None:
    my_step.flow_file("query", "q.sql", "DELETE FROM items")

    assert my_step.run() == 1

    assert "READ ONLY transaction" in capsys.readouterr().err


def test_one_statement_only(
    my_step: Step, server: tuple[str, pymysql.Connection[Any]], capsys: pytest.CaptureFixture[str]
) -> None:
    my_step.flow_file("query", "q.sql", "SELECT 1 AS one; DROP TABLE items")

    assert my_step.run() == 1

    assert "the database refused the query" in capsys.readouterr().err
    run_sql(server, "SELECT 1 FROM items")


def test_a_statement_over_the_timeout_is_stopped(
    my_step: Step, capsys: pytest.CaptureFixture[str]
) -> None:
    # SLEEP() interrupted by MySQL returns 1 without an error: a long join instead.
    my_step.flow_file("query", "q.sql", LONG_JOIN)
    my_step.inputs["timeout"] = 1

    assert my_step.run() == 1

    assert "interrupted" in capsys.readouterr().err


def test_the_next_run_reads_what_follows(
    my_step: Step, server: tuple[str, pymysql.Connection[Any]]
) -> None:
    run_sql(server, ROW)
    run_sql(server, ROW.replace("(1,", "(2,"))
    my_step.flow_file("query", "q.sql", "SELECT id, at FROM items")
    my_step.inputs.update(cursor="at", key=["id"])
    assert my_step.run() == 0
    assert [r["id"] for r in my_step.records] == [1, 2]
    run_sql(server, ROW.replace("(1,", "(3,"))
    run_sql(server, ROW.replace("(1,", "(4,").replace("08:00:00", "09:00:00"))

    assert my_step.next_run() == 0

    assert [r["id"] for r in my_step.records] == [3, 4]
    assert my_step.state is not None
    assert my_step.state["streams"]["records"]["cursor"] == "2026-11-20T09:00:00"


def test_lookback_reads_again_under_a_datetime_cursor(
    my_step: Step, server: tuple[str, pymysql.Connection[Any]]
) -> None:
    run_sql(server, ROW)
    my_step.flow_file("query", "q.sql", "SELECT id, at FROM items")
    my_step.inputs.update(cursor="at", key=["id"], lookback=3600)
    assert my_step.run() == 0
    run_sql(server, ROW.replace("(1,", "(2,").replace("08:00:00", "07:30:00"))

    assert my_step.next_run() == 0

    assert [r["id"] for r in my_step.records] == [2, 1]
