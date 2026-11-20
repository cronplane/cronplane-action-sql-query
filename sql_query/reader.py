"""Reads the result of the query in a read-only transaction, batch by batch."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Final

import psycopg
from sqlalchemy import Connection, CursorResult, create_engine
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.pool import NullPool

from .columns import DriverColumn
from .database import Database, Kind
from .errors import SqlQueryError
from .query import Query

CONNECT_TIMEOUT: Final = 10
# MySQL's field types (pymysql.constants.FIELD_TYPE) by the name the type table uses.
MYSQL_TYPES: Final = {
    1: "tinyint", 2: "smallint", 3: "int", 8: "bigint", 9: "mediumint", 13: "year",
    0: "decimal", 246: "decimal", 4: "float", 5: "double",
    10: "date", 12: "datetime", 7: "timestamp", 11: "time", 245: "json",
    16: "bit", 255: "geometry",
}  # fmt: skip
MYSQL_TEXTS: Final = {
    254: "char",
    253: "varchar",
    252: "text",
    249: "text",
    250: "text",
    251: "text",
}
MYSQL_BINARIES: Final = {
    254: "binary",
    253: "varbinary",
    252: "blob",
    249: "blob",
    250: "blob",
    251: "blob",
}
MYSQL_BINARY_CHARSET: Final = 63
MYSQL_ENUM_FLAG: Final = 256
MYSQL_SET_FLAG: Final = 2048

Row = Sequence[Any]


@dataclass(frozen=True)
class Result:
    columns: list[DriverColumn]
    rows: Iterator[Row]


def _postgres_column(
    description: psycopg.Column, connection: psycopg.Connection[Any]
) -> DriverColumn:
    oid: int = description.type_code
    info = connection.adapters.types.get(oid)
    if info is None:
        shown = connection.execute("SELECT format_type(%s, NULL)", [oid]).fetchone()
        return DriverColumn(description.name, None, shown=shown[0] if shown else None)
    array = info.array_oid == oid and info.oid != oid
    return DriverColumn(description.name, info.name, array=array, length=description.display_size)


def mysql_type(type_code: int, charset: int, flags: int, length: int) -> str | None:
    """The name of a MySQL column's type, from what the protocol says of it."""
    if type_code == 1 and length == 1:
        return "bool"
    if type_code in MYSQL_TEXTS:
        if charset == MYSQL_BINARY_CHARSET:
            return MYSQL_BINARIES[type_code]
        if flags & MYSQL_ENUM_FLAG:
            return "enum"
        if flags & MYSQL_SET_FLAG:
            return "set"
        return MYSQL_TEXTS[type_code]
    return MYSQL_TYPES.get(type_code)


def _mysql_columns(cursor: Any) -> list[DriverColumn]:  # noqa: ANN401 # PyMySQL's unbuffered cursor
    # The charset and the flags of a column are only in PyMySQL's fields.
    fields: list[Any] = cursor._result.fields
    columns: list[DriverColumn] = []
    for field in fields:
        name = mysql_type(field.type_code, field.charsetnr, field.flags, field.length)
        shown = name if name else f"code {field.type_code}"
        columns.append(DriverColumn(field.name, name, shown=shown, database=Kind.MYSQL))
    return columns


def _mysql_value(type_name: str | None) -> Callable[[Any], Any] | None:
    if type_name == "bool":
        return lambda v: bool(v) if v in (0, 1) else v
    if type_name == "json":
        return lambda v: json.loads(v) if isinstance(v, str) else v
    return None


def _mysql_rows(columns: list[DriverColumn], rows: Iterator[Row]) -> Iterator[Row]:
    fixes = [_mysql_value(c.type_name) for c in columns]
    if not any(fixes):
        yield from rows
        return
    for row in rows:
        yield [fix(v) if fix and v is not None else v for fix, v in zip(fixes, row, strict=True)]


def _failure(error: SQLAlchemyError) -> SqlQueryError:
    original = error.orig if isinstance(error, DBAPIError) else None
    message = str(original or error).strip().splitlines()
    return SqlQueryError(f"the database refused the query: {' '.join(message[:3])}")


def _start(connection: Connection, kind: Kind, timeout: int) -> None:
    """A read-only transaction where each statement takes `timeout` seconds at most."""
    if kind is Kind.POSTGRES:
        connection.exec_driver_sql(f"SET LOCAL statement_timeout = {timeout * 1000}")
        return
    version = str(connection.exec_driver_sql("SELECT VERSION()").scalar_one())
    if "MariaDB" in version:
        connection.exec_driver_sql(f"SET SESSION max_statement_time = {timeout}")
    else:
        connection.exec_driver_sql(f"SET SESSION max_execution_time = {timeout * 1000}")
    connection.exec_driver_sql("SET SESSION TRANSACTION READ ONLY")


def _columns(result: CursorResult[Any], connection: Connection, kind: Kind) -> list[DriverColumn]:
    if kind is Kind.MYSQL:
        return _mysql_columns(result.cursor)
    raw = connection.connection.dbapi_connection
    if not isinstance(raw, psycopg.Connection) or result.cursor is None:
        raise SqlQueryError("the query gives no rows (is it a SELECT?)")
    return [_postgres_column(d, raw) for d in result.cursor.description or ()]


@contextmanager
def read(
    database: Database, address: str, query: Query, *, timeout: int, batch_size: int
) -> Iterator[Result]:
    """The columns and rows of the query, read from `address`, the checked
    address of the database's host, in a read-only transaction where each
    statement takes `timeout` seconds at most."""
    url, arguments = database.connect(address, CONNECT_TIMEOUT)
    engine = create_engine(url, poolclass=NullPool, connect_args=arguments)
    try:
        with engine.connect() as opened:
            connection = opened
            if database.kind is Kind.MYSQL:
                # Session settings, for the transaction that follows.
                _start(connection, database.kind, timeout)
                connection.commit()
            else:
                connection = opened.execution_options(postgresql_readonly=True)
            with connection.begin():
                if database.kind is Kind.POSTGRES:
                    _start(connection, database.kind, timeout)
                result = connection.execution_options(
                    stream_results=True, yield_per=batch_size
                ).execute(query.clause, query.params)
                try:
                    columns = _columns(result, connection, database.kind)
                    rows: Iterator[Row] = iter(result)
                    if database.kind is Kind.MYSQL:
                        rows = _mysql_rows(columns, rows)
                    yield Result(columns, rows)
                finally:
                    result.close()
    except SQLAlchemyError as e:
        raise _failure(e) from None
    finally:
        engine.dispose()
