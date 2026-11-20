"""A value of the driver as a JSON value of the record."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import pytest

from sql_query.errors import Rejected
from sql_query.values import to_json


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (True, True),
        (Decimal("1.50"), 1.5),
        (Decimal("12"), 12),
        (Decimal("1E+2"), 100),
        (1.5, 1.5),
        (dt.datetime(2026, 11, 20, 8, 0, tzinfo=dt.UTC), "2026-11-20T08:00:00+00:00"),
        (dt.date(2026, 11, 20), "2026-11-20"),
        (dt.time(8, 0), "08:00:00"),
        (dt.timedelta(hours=8), "08:00:00"),
        (dt.timedelta(hours=-1, minutes=-30), "-01:30:00"),
        (dt.timedelta(hours=838, minutes=59, seconds=59), "838:59:59"),
        (dt.timedelta(seconds=1, microseconds=5), "00:00:01.000005"),
        (uuid.UUID(int=1), "00000000-0000-0000-0000-000000000001"),
        ([Decimal("1"), None], [1, None]),
        ({"a": Decimal("0.5")}, {"a": 0.5}),
    ],
)
def test_a_driver_value_becomes_json(value: object, expected: object) -> None:
    assert to_json(value) == expected


@pytest.mark.parametrize("value", [Decimal("NaN"), float("inf"), b"\x00"])
def test_a_value_without_json_form_is_rejected(value: object) -> None:
    with pytest.raises(Rejected):
        to_json(value)
