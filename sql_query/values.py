"""A value of the driver as a JSON value of the record."""

from __future__ import annotations

import datetime as dt
import math
import uuid
from decimal import Decimal

from cronplane_actions import JsonValue

from .errors import Rejected


def _number(value: Decimal | float) -> JsonValue:
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise Rejected(f"{value} is not a JSON number")
        exponent = value.as_tuple().exponent
        if isinstance(exponent, int) and exponent >= 0:
            return int(value)
        return float(value)
    if not math.isfinite(value):
        raise Rejected(f"{value} is not a JSON number")
    return value


def _text(value: object) -> str:
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    raise Rejected(f"a value of type {type(value).__name__} has no JSON form")


def to_json(value: object) -> JsonValue:
    """The JSON value of `value`; `Rejected` when JSON has none (NaN)."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, Decimal | float):
        return _number(value)
    if isinstance(value, list | tuple):
        return [to_json(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_json(item) for key, item in value.items()}
    return _text(value)
