"""The action from end to end: a query of the flow read into the stream `records`."""

from __future__ import annotations

from typing import Any

import psycopg
import pytest

from tests.conftest import Step

CLIENTS = (
    "INSERT INTO customers VALUES "
    "(1, 'ada@ex.fr', '00012345600019', '2026-11-19 09:00+01', 1234.56, '{\"newsletter\": true}'),"
    "(2, 'bob@ex.fr', NULL, '2026-11-19 10:00+01', NULL, NULL)"
)


def test_the_rows_become_records_with_the_drivers_types(
    step: Step, db: psycopg.Connection[Any]
) -> None:
    db.execute(CLIENTS)
    step.flow_file("query", "sql/clients.sql", "SELECT * FROM customers ORDER BY id")

    assert step.run() == 0

    assert step.output == {"rows": 2, "rejected": 0}
    assert step.schema["stream"] == "records"
    assert step.schema["schema"]["properties"] == {
        "id": {"type": ["integer", "null"]},
        "email": {"type": ["string", "null"], "maxLength": 255},
        "siret": {"type": ["string", "null"]},
        "updated_at": {"type": ["string", "null"], "format": "date-time"},
        "total": {"type": ["number", "null"]},
        "prefs": {},
    }
    assert step.records == [
        {"id": 1, "email": "ada@ex.fr", "siret": "00012345600019",
         "updated_at": "2026-11-19T08:00:00+00:00", "total": 1234.56,
         "prefs": {"newsletter": True}},
        {"id": 2, "email": "bob@ex.fr", "siret": None,
         "updated_at": "2026-11-19T09:00:00+00:00", "total": None, "prefs": None},
    ]  # fmt: skip


def test_an_empty_result_still_has_its_schema(step: Step, db: psycopg.Connection[Any]) -> None:
    step.flow_file("query", "q.sql", "SELECT id, email FROM customers")

    assert step.run() == 0

    assert step.output == {"rows": 0, "rejected": 0}
    assert set(step.schema["schema"]["properties"]) == {"id", "email"}
    assert step.records == []


def test_params_are_bound_never_written_into_the_query(
    step: Step, db: psycopg.Connection[Any]
) -> None:
    db.execute(CLIENTS)
    step.flow_file("query", "q.sql", "SELECT id FROM customers WHERE email = :email")
    step.inputs["params"] = {"email": "x' OR '1'='1"}

    assert step.run() == 0

    assert step.records == []


def test_a_param_selects_rows(step: Step, db: psycopg.Connection[Any]) -> None:
    db.execute(CLIENTS)
    step.flow_file("query", "q.sql", "SELECT id FROM customers WHERE email = :email")
    step.inputs["params"] = {"email": "bob@ex.fr"}

    assert step.run() == 0

    assert step.records == [{"id": 2}]


def test_the_types_file_corrects_some_columns(step: Step, db: psycopg.Connection[Any]) -> None:
    db.execute(CLIENTS)
    step.flow_file("query", "q.sql", "SELECT id, siret FROM customers ORDER BY id")
    step.flow_file("types", "clients.yaml", "columns:\n  siret: {type: string, minLength: 14}\n")
    step.inputs["max_rejected"] = 1

    assert step.run() == 0

    assert step.schema["schema"]["properties"]["siret"] == {"type": "string", "minLength": 14}
    assert step.records == [{"id": 1, "siret": "00012345600019"}]
    assert [r["record"] for r in step.rejected] == [{"id": 2, "siret": None}]
    assert step.output == {"rows": 1, "rejected": 1}


def test_a_decimal_column_typed_string_keeps_every_digit(
    step: Step, db: psycopg.Connection[Any]
) -> None:
    step.flow_file("query", "q.sql", "SELECT 12345678901234567.89::numeric AS total, 1.5 AS rate")
    step.flow_file("types", "t.yaml", "columns:\n  total: {type: string}\n")

    assert step.run() == 0

    assert step.records == [{"total": "12345678901234567.89", "rate": 1.5}]


def test_more_rejected_rows_than_allowed_fail_the_step(
    step: Step, db: psycopg.Connection[Any], capsys: pytest.CaptureFixture[str]
) -> None:
    db.execute(CLIENTS)
    step.flow_file("query", "q.sql", "SELECT id, siret FROM customers")
    step.flow_file("types", "t.json", '{"columns": {"siret": {"type": "string"}}}')

    assert step.run() == 1

    assert "1 records rejected, 0 allowed" in capsys.readouterr().err


def test_a_value_without_json_form_rejects_its_row(step: Step, db: psycopg.Connection[Any]) -> None:
    step.flow_file("query", "q.sql", "SELECT 1 AS id, 'NaN'::numeric AS n")
    step.inputs["max_rejected"] = 1

    assert step.run() == 0

    assert step.rejected == [
        {"record": {"id": 1, "n": "NaN"}, "reason": "n: NaN is not a JSON number"}
    ]


def test_a_type_out_of_the_table_is_a_clear_error(
    step: Step, db: psycopg.Connection[Any], capsys: pytest.CaptureFixture[str]
) -> None:
    step.flow_file("query", "q.sql", "SELECT '1 day'::interval AS wait")

    assert step.run() == 1

    assert "column wait: the PostgreSQL type interval has no JSON type" in capsys.readouterr().err


def test_the_types_file_gives_a_type_out_of_the_table(
    step: Step, db: psycopg.Connection[Any]
) -> None:
    step.flow_file("query", "q.sql", "SELECT 'a'::name AS label")
    step.flow_file("types", "t.yaml", "columns:\n  label: {type: string}\n")
    step.inputs["max_rejected"] = 0

    assert step.run() == 0

    assert step.records == [{"label": "a"}]


def test_only_a_query_that_reads_runs(
    step: Step, db: psycopg.Connection[Any], capsys: pytest.CaptureFixture[str]
) -> None:
    step.flow_file("query", "q.sql", "DELETE FROM customers RETURNING id")

    assert step.run() == 1

    assert 'syntax error at or near "DELETE"' in capsys.readouterr().err


def test_the_transaction_is_read_only(
    step: Step, db: psycopg.Connection[Any], capsys: pytest.CaptureFixture[str]
) -> None:
    db.execute("DROP SEQUENCE IF EXISTS numbers")
    db.execute("CREATE SEQUENCE numbers")
    step.flow_file("query", "q.sql", "SELECT nextval('numbers') AS n")

    assert step.run() == 1

    assert "cannot execute nextval() in a read-only transaction" in capsys.readouterr().err


def test_a_statement_over_the_timeout_is_stopped(
    step: Step, db: psycopg.Connection[Any], capsys: pytest.CaptureFixture[str]
) -> None:
    step.flow_file("query", "q.sql", "SELECT pg_sleep(3) AS slept")
    step.inputs["timeout"] = 1

    assert step.run() == 1

    assert "statement timeout" in capsys.readouterr().err


def test_without_allowed_hosts_the_step_says_what_to_add(
    step: Step, capsys: pytest.CaptureFixture[str]
) -> None:
    step.allow = None
    step.flow_file("query", "q.sql", "SELECT 1 AS one")

    assert step.run() == 1

    assert 'network: {allow: ["127.0.0.1:55432"]}' in capsys.readouterr().err


def test_a_host_out_of_the_list_is_refused(step: Step, capsys: pytest.CaptureFixture[str]) -> None:
    step.allow = "db.acme.internal:5432"
    step.flow_file("query", "q.sql", "SELECT 1 AS one")

    assert step.run() == 1

    assert "127.0.0.1:55432 is not one of the hosts this step may reach" in capsys.readouterr().err


def test_the_password_is_masked(step: Step, db: psycopg.Connection[Any]) -> None:
    step.flow_file("query", "q.sql", "SELECT 1 AS one")

    assert step.run() == 0

    assert (step.root / "mask").read_text().splitlines() == [step.url.split(":")[2].split("@")[0]]


def test_a_query_the_database_refuses_says_why(
    step: Step, db: psycopg.Connection[Any], capsys: pytest.CaptureFixture[str]
) -> None:
    step.flow_file("query", "q.sql", "SELECT nope FROM customers")

    assert step.run() == 1

    assert 'the database refused the query: column "nope" does not exist' in capsys.readouterr().err


def test_a_type_the_driver_does_not_know_is_named_by_the_database(
    step: Step, db: psycopg.Connection[Any], capsys: pytest.CaptureFixture[str]
) -> None:
    db.execute("DROP TYPE IF EXISTS mood")
    db.execute("CREATE TYPE mood AS ENUM ('calm', 'angry')")
    step.flow_file("query", "q.sql", "SELECT 'calm'::mood AS mood")

    assert step.run() == 1

    assert "column mood: the PostgreSQL type mood has no JSON type" in capsys.readouterr().err
