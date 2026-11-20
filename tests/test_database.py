"""The database of DATABASE_URL: PostgreSQL and safe options only."""

from __future__ import annotations

import ssl

import pytest

from sql_query.database import Database, Kind
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


def test_a_mysql_url_gives_its_host_and_default_port() -> None:
    database = Database.parse("mysql+pymysql://u:p@db.acme.internal/crm?charset=utf8mb4")
    assert (database.kind, database.port, database.tls) == (Kind.MYSQL, 3306, False)
    url, arguments = database.connect("10.0.0.5", 10)
    assert (url.host, arguments) == ("10.0.0.5", {"connect_timeout": 10})


def test_mysql_verify_full_checks_the_certificate_against_the_host_name() -> None:
    database = Database.parse("mysql+pymysql://u:p@db.acme.internal/crm?sslmode=verify-full")
    url, arguments = database.connect("10.0.0.5", 10)
    assert database.tls
    assert url.host == "db.acme.internal"
    assert "sslmode" not in url.query
    context = arguments["ssl"]
    assert isinstance(context, ssl.SSLContext)
    assert context.check_hostname
    assert context.verify_mode == ssl.CERT_REQUIRED


def test_a_postgres_url_connects_to_the_checked_address() -> None:
    database = Database.parse("postgresql+psycopg://u:p@db.acme.internal/crm")
    url, arguments = database.connect("10.0.0.5", 10)
    assert (url.host, arguments) == (
        "db.acme.internal",
        {"hostaddr": "10.0.0.5", "connect_timeout": 10},
    )


@pytest.mark.parametrize(
    "option", ["sslmode=require", "ssl_ca=/etc/ca.pem", "init_command=SET+x%3D1", "charset=latin1"]
)
def test_an_unsafe_mysql_option_is_refused(option: str) -> None:
    with refused(f"the option {option.partition('=')[0]} is not allowed for MySQL"):
        Database.parse(f"mysql+pymysql://u:p@h/x?{option}")
