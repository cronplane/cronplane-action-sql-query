"""Reading from where the last successful run stopped: the action wraps the
query with the condition on the cursor and the key, and keeps the last row
read in the state of the step."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from cronplane_actions import JsonValue
from sqlalchemy import (
    BindParameter,
    bindparam,
    column,
    literal_column,
    or_,
    select,
    text,
    true,
    tuple_,
)
from sqlalchemy.types import NullType

from .columns import DriverColumn
from .errors import SqlQueryError
from .query import RESERVED, Query

#: The types a cursor may have: they order, and their JSON value reads back.
CURSOR_TYPES: Final = frozenset(
    {"int2", "int4", "int8", "numeric", "date", "timestamp", "timestamptz", "text", "varchar"}
    | {"tinyint", "smallint", "mediumint", "int", "bigint", "decimal", "datetime", "char"}
)
DATE_TIMES: Final = frozenset({"timestamp", "timestamptz", "datetime"})


@dataclass(frozen=True)
class Plan:
    """What the step was asked: `cursor` and `key`, the columns of the result."""

    cursor: str | None
    key: tuple[str, ...]
    start: str | None
    lookback: int

    def __post_init__(self) -> None:
        if self.cursor is not None and not self.key:
            raise SqlQueryError(
                "cursor needs key: the columns that tell apart the rows of the same cursor"
            )
        if self.cursor in self.key:
            raise SqlQueryError(f"key: {self.cursor} is the cursor already")
        if len(set(self.key)) != len(self.key):
            raise SqlQueryError("key: a column is named twice")
        if self.cursor is None and (self.start is not None or self.lookback):
            raise SqlQueryError("start and lookback need cursor")

    @property
    def columns(self) -> dict[str, JsonValue]:
        return {"cursor": self.cursor, "key": list(self.key)}


@dataclass(frozen=True)
class Mark:
    """The last row read: its cursor and its key, as the state keeps them."""

    cursor: JsonValue
    key: list[JsonValue]


def last_mark(plan: Plan, kept: Mapping[str, JsonValue], step: str) -> Mark | None:
    """Where the last successful run stopped; `None` at the first run."""
    if not kept:
        return None
    if kept.get("columns") != plan.columns:
        shown = kept.get("columns")
        raise SqlQueryError(
            f"the state was written for {shown}, the step reads {plan.columns}; "
            f"reset it: cronplane state reset <flow> --step {step}"
        )
    key = kept.get("last_key")
    if not isinstance(key, list) or len(key) != len(plan.key):
        raise SqlQueryError(f"the state holds no last key for {plan.key}; reset it")
    return Mark(kept.get("cursor"), key)


def _since(mark: Mark, lookback: int) -> dt.datetime:
    if isinstance(mark.cursor, str):
        try:
            return dt.datetime.fromisoformat(mark.cursor) - dt.timedelta(seconds=lookback)
        except ValueError:
            pass
    raise SqlQueryError(
        f"lookback needs a cursor of date and time: the state holds {mark.cursor!r}"
    )


def check_columns(plan: Plan, columns: Sequence[DriverColumn]) -> None:
    """The cursor is of a type that orders and reads back from the state (the
    database already refused a cursor or a key that is not a column)."""
    found = next((c for c in columns if c.name == plan.cursor), None)
    if found is None:
        return
    if found.array or found.type_name not in CURSOR_TYPES:
        raise SqlQueryError(
            f"cursor: {plan.cursor} is of type {found.type_name or found.shown}; "
            f"a cursor is one of {', '.join(sorted(CURSOR_TYPES))}"
        )
    if plan.lookback and found.type_name not in DATE_TIMES:
        raise SqlQueryError(
            f"lookback needs a cursor of date and time: {plan.cursor} is {found.type_name}"
        )


def wrap(query: Query, plan: Plan, mark: Mark | None) -> Query:
    """The query of the step around the author's: rows after `mark` (or from
    `start`), and rows without cursor (to be rejected), in the order of the
    cursor and the key."""
    if plan.cursor is None:
        return query

    def bound(name: str, value: Any) -> BindParameter[Any]:  # noqa: ANN401 # a value of the state
        # Untyped: the database reads it as the type of the column it meets.
        return bindparam(f"{RESERVED}{name}", value, type_=NullType())

    named = [plan.cursor, *plan.key]
    inner = text(query.source.strip().rstrip(";")).columns(*(column(n) for n in named))
    q = inner.subquery("q")
    cursor, keys = q.c[plan.cursor], [q.c[k] for k in plan.key]
    if mark is not None and plan.lookback:
        condition = cursor > bound("since", _since(mark, plan.lookback))
    elif mark is not None:
        after = [bound("cursor", mark.cursor)] + [
            bound(f"key_{i}", value) for i, value in enumerate(mark.key)
        ]
        condition = tuple_(cursor, *keys) > tuple_(*after)
    elif plan.start is not None:
        condition = cursor >= bound("start", plan.start)
    else:
        condition = true()
    statement = (
        select(literal_column("*"))
        .select_from(q)
        .where(or_(condition, cursor.is_(None)))
        .order_by(cursor, *keys)
    )
    return Query(query.source, statement, query.params)


def advanced(plan: Plan, mark: Mark | None, row: Mark) -> Mark:
    """Where the next run starts: the row read, unless a lookback read it
    again under the mark already kept."""
    if mark is None or not plan.lookback:
        return row
    # With lookback, the cursor is a date and time (check_columns): ISO text.
    new: tuple[Any, ...] = (dt.datetime.fromisoformat(str(row.cursor)), *row.key)
    kept: tuple[Any, ...] = (dt.datetime.fromisoformat(str(mark.cursor)), *mark.key)
    return row if new > kept else mark
