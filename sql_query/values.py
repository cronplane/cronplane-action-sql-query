"""A value of the driver as a JSON value of the record."""

from __future__ import annotations

import datetime as dt
import math
import uuid
from collections.abc import Mapping
from decimal import Decimal

from cronplane_actions import JsonValue

from .errors import Rejected


def _number(value: Decimal | float, *, decimal_text: bool) -> JsonValue:
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise Rejected(f"{value} is not a JSON number")
        if decimal_text:
            return format(value, "f")
        exponent = value.as_tuple().exponent
        if isinstance(exponent, int) and exponent >= 0:
            return int(value)
        return float(value)
    if not math.isfinite(value):
        raise Rejected(f"{value} is not a JSON number")
    return value


def _duration(value: dt.timedelta) -> str:
    """A MySQL `TIME`, which may be negative or over 24 hours: `-838:59:59`."""
    sign, seconds = ("-", -value) if value < dt.timedelta(0) else ("", value)
    minutes, second = divmod(seconds.seconds, 60)
    hours = seconds.days * 24 + minutes // 60
    fraction = f".{seconds.microseconds:06d}" if seconds.microseconds else ""
    return f"{sign}{hours:02d}:{minutes % 60:02d}:{second:02d}{fraction}"


def _text(value: object) -> str:
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return _duration(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    raise Rejected(f"a value of type {type(value).__name__} has no JSON form")


def decimal_text(schema: Mapping[str, JsonValue]) -> bool:
    """Whether a column of `schema` takes a decimal as its exact text: its
    type allows a string and no number (`total: {type: string}`)."""
    kind = schema.get("type")
    kinds = set(kind) if isinstance(kind, list) else {kind}
    return "string" in kinds and not kinds & {"number", "integer"}


def to_json(value: object, *, decimal_text: bool = False) -> JsonValue:
    """The JSON value of `value`; `Rejected` when JSON has none (NaN). With
    `decimal_text`, a decimal is its exact text (`"12345678901234567.89"`)."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, Decimal | float):
        return _number(value, decimal_text=decimal_text)
    if isinstance(value, list | tuple):
        return [to_json(item, decimal_text=decimal_text) for item in value]
    if isinstance(value, dict):
        return {str(key): to_json(item) for key, item in value.items()}
    return _text(value)
