"""Reads the result of the query in a read-only transaction, batch by batch."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import psycopg
from sqlalchemy import create_engine
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.pool import NullPool

from .columns import DriverColumn
from .database import Database
from .errors import SqlQueryError
from .query import Query

CONNECT_TIMEOUT = 10


@dataclass(frozen=True)
class Result:
    columns: list[DriverColumn]
    rows: Iterator[Sequence[Any]]


def _column(description: psycopg.Column, connection: psycopg.Connection[Any]) -> DriverColumn:
    oid: int = description.type_code
    info = connection.adapters.types.get(oid)
    if info is None:
        shown = connection.execute("SELECT format_type(%s, NULL)", [oid]).fetchone()
        return DriverColumn(description.name, None, shown=shown[0] if shown else None)
    array = info.array_oid == oid and info.oid != oid
    return DriverColumn(description.name, info.name, array=array, length=description.display_size)


def _failure(error: SQLAlchemyError) -> SqlQueryError:
    original = error.orig if isinstance(error, DBAPIError) else None
    message = str(original or error).strip().splitlines()
    return SqlQueryError(f"the database refused the query: {' '.join(message[:3])}")


@contextmanager
def read(
    database: Database, address: str, query: Query, *, timeout: int, batch_size: int
) -> Iterator[Result]:
    """The columns and rows of the query, read from `address`, the checked
    address of the database's host, in a read-only transaction where each
    statement takes `timeout` seconds at most."""
    engine = create_engine(
        database.url,
        poolclass=NullPool,
        connect_args={"hostaddr": address, "connect_timeout": CONNECT_TIMEOUT},
    )
    try:
        with engine.connect() as opened:
            connection = opened.execution_options(postgresql_readonly=True)
            with connection.begin():
                connection.exec_driver_sql(f"SET LOCAL statement_timeout = {timeout * 1000}")
                result = connection.execution_options(
                    stream_results=True, yield_per=batch_size
                ).execute(query.clause, query.params)
                try:
                    raw = connection.connection.dbapi_connection
                    if not isinstance(raw, psycopg.Connection) or result.cursor is None:
                        raise SqlQueryError("the query gives no rows (is it a SELECT?)")
                    columns = [_column(d, raw) for d in result.cursor.description or ()]
                    yield Result(columns, iter(result))
                finally:
                    result.close()
    except SQLAlchemyError as e:
        raise _failure(e) from None
    finally:
        engine.dispose()
