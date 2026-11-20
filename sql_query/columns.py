"""The JSON type of each column of the result, from the type the driver gives,
by a closed table: a type out of it is an error until the types file gives it."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from cronplane_actions import JsonValue

from .errors import SqlQueryError


@dataclass(frozen=True)
class DriverColumn:
    """What the driver says of a column of the result."""

    name: str
    #: The PostgreSQL name of its type (`int4`, `varchar`); `None`: unknown to the driver.
    type_name: str | None
    #: Its elements are of `type_name` (`int4[]`).
    array: bool = False
    #: The length a `varchar(n)` or `char(n)` declares.
    length: int | None = None
    #: Shown in an error, for a type out of the table (`geometry`).
    shown: str | None = None


def _plain(kind: str) -> Callable[[DriverColumn], dict[str, JsonValue]]:
    return lambda _column: {"type": kind}


def _text(column: DriverColumn) -> dict[str, JsonValue]:
    if column.length is not None and not column.array:
        return {"type": "string", "maxLength": column.length}
    return {"type": "string"}


# Each PostgreSQL type with the JSON Schema of its values (`"null"` is added).
POSTGRES: Final[Mapping[str, Callable[[DriverColumn], dict[str, JsonValue]]]] = {
    "int2": _plain("integer"),
    "int4": _plain("integer"),
    "int8": _plain("integer"),
    "numeric": _plain("number"),
    "float4": _plain("number"),
    "float8": _plain("number"),
    "bool": _plain("boolean"),
    "text": _text,
    "varchar": _text,
    "bpchar": _text,
    "uuid": _plain("string"),
    "timestamptz": lambda _column: {"type": "string", "format": "date-time"},
    "timestamp": _plain("string"),
    "date": _plain("string"),
    "time": _plain("string"),
    "timetz": _plain("string"),
    "json": lambda _column: {},
    "jsonb": lambda _column: {},
}


def _nullable(schema: dict[str, JsonValue]) -> dict[str, JsonValue]:
    kind = schema.get("type")
    if isinstance(kind, str):
        return {**schema, "type": [kind, "null"]}
    return schema


def driver_schema(column: DriverColumn) -> dict[str, JsonValue] | None:
    """The schema of the column's values; `None` for a type out of the table."""
    build = POSTGRES.get(column.type_name or "")
    if build is None:
        return None
    if column.array:
        return {"type": ["array", "null"], "items": _nullable(build(column))}
    return _nullable(build(column))


def record_schema(
    columns: Sequence[DriverColumn], corrections: Mapping[str, dict[str, JsonValue]]
) -> dict[str, JsonValue]:
    """The schema of a record: one property per column, the driver's type
    unless the types file gives the column's."""
    names = [c.name for c in columns]
    twice = sorted({n for n in names if names.count(n) > 1})
    if twice:
        raise SqlQueryError(
            f"the query gives the column {', '.join(twice)} twice: name each column once (AS)"
        )
    unknown = sorted(set(corrections) - set(names))
    if unknown:
        raise SqlQueryError(
            f"types file: the column {', '.join(unknown)} is not in the result "
            f"(columns: {', '.join(names)})"
        )
    properties: dict[str, JsonValue] = {}
    for column in columns:
        schema = corrections[column.name] if column.name in corrections else driver_schema(column)
        if schema is None:
            shown = column.shown or column.type_name or "unknown"
            raise SqlQueryError(
                f"column {column.name}: the PostgreSQL type {shown} has no JSON type; "
                f"cast it in the query ({column.name}::text AS {column.name}) "
                "or give its type in the types file"
            )
        properties[column.name] = schema
    return {"type": "object", "properties": properties}
