"""The database of DATABASE_URL: PostgreSQL and safe options only."""

from __future__ import annotations

import pytest

from sql_query.database import Database
from tests.helpers import refused


def test_a_postgres_url_gives_its_host_and_port() -> None:
    database = Database.parse("postgresql+psycopg://u:p@db.acme.internal/crm?sslmode=verify-full")
    assert (database.host, database.port, database.allow_line) == (
        "db.acme.internal", 5432, "db.acme.internal:5432"
    )  # fmt: skip


def test_an_ipv6_host_is_written_in_brackets() -> None:
    assert Database.parse("postgresql+psycopg://u:p@[::1]:6543/x").allow_line == "[::1]:6543"


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("not a url", "not a database URL"),
        ("mysql+mysqldb://u:p@h/x", "the driver mysql\\+mysqldb is not one of"),
        ("postgresql+psycopg://u:p@/x?host=/tmp", "one host name or address"),
        ("postgresql+psycopg://u:p@h/x?sslkey=/etc/key", "the option sslkey is not allowed"),
        ("postgresql+psycopg://u:p@h/x?sslrootcert=/etc/ca", "the option sslrootcert"),
        ("postgresql+psycopg://u:p@h/x?sslmode=sometimes", "the option sslmode"),
    ],
)
def test_an_unsafe_url_is_refused(url: str, message: str) -> None:
    with refused(message):
        Database.parse(url)


def test_sslrootcert_system_is_allowed() -> None:
    assert Database.parse("postgresql+psycopg://u:p@h/x?sslrootcert=system").host == "h"
