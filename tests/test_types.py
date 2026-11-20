"""The JSON type of each column: the driver's, or the types file's."""

from __future__ import annotations

from pathlib import Path

import pytest

from sql_query import corrections
from sql_query.columns import DriverColumn, driver_schema, record_schema
from sql_query.database import Kind
from sql_query.reader import mysql_type
from tests.helpers import refused


def test_a_yaml_types_file_gives_schemas() -> None:
    text = "columns:\n  siret: {type: [string, 'null'], maxLength: 14}\n  prefs: {}\n"
    assert corrections.parse(text, "t.yaml") == {
        "siret": {"type": ["string", "null"], "maxLength": 14},
        "prefs": {},
    }


def test_a_json_types_file_gives_schemas() -> None:
    assert corrections.parse('{"columns": {"n": {"type": "integer"}}}', "t.json") == {
        "n": {"type": "integer"}
    }


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (
            "columns:\n  pays: {enum: [fr, no]}\n",
            'line 2: no is read differently.*quote it \\("no"\\)',
        ),
        ("columns:\n  a: {enum: [On]}\n", "On is read differently"),
        ("columns:\n  yes: {type: string}\n", "yes is read differently"),
        ("columns:\n  d: {default: 2026-01-01}\n", "is not a JSON value \\(a date\\?\\); quote it"),
        ("columns:\n  n: {maximum: .nan}\n", "nan is not a JSON number"),
        ("columns:\n  1: {type: string}\n", "the key 1 is not text"),
        ("columns: [a]\n", "columns: maps each column to its schema"),
        ("columns:\n  a: string\n", "column a: a schema is a mapping"),
        ("other: {}\n", "one key is expected, columns:"),
        ("columns: {a: [}\n", "not valid YAML"),
    ],
)
def test_a_bad_yaml_types_file_is_refused(text: str, message: str) -> None:
    with refused(message):
        corrections.parse(text, "t.yaml")


def test_quoted_and_canonical_values_are_kept() -> None:
    text = "columns:\n  n: {enum: ['no', \"on\", y], default: true}\n"
    assert corrections.parse(text, "t.yaml") == {"n": {"enum": ["no", "on", "y"], "default": True}}


def test_a_bad_json_types_file_is_refused() -> None:
    with refused("not valid JSON"):
        corrections.parse('{"columns": {"n": {"maximum": NaN}}}', "t.json")


def test_a_types_file_is_read_from_disk(tmp_path: Path) -> None:
    (tmp_path / "t.yaml").write_text("columns: {}\n")
    assert corrections.read(tmp_path / "t.yaml") == {}
    (tmp_path / "big.yaml").write_bytes(b"#" * (corrections.MAX_BYTES + 1))
    with refused("more than"):
        corrections.read(tmp_path / "big.yaml")
    (tmp_path / "bin.yaml").write_bytes(b"\xff")
    with refused("not UTF-8"):
        corrections.read(tmp_path / "bin.yaml")
    with refused("cannot be read"):
        corrections.read(tmp_path / "none.yaml")


@pytest.mark.parametrize(
    ("column", "schema"),
    [
        (DriverColumn("a", "int8"), {"type": ["integer", "null"]}),
        (DriverColumn("a", "numeric"), {"type": ["number", "null"]}),
        (DriverColumn("a", "bool"), {"type": ["boolean", "null"]}),
        (DriverColumn("a", "varchar", length=5), {"type": ["string", "null"], "maxLength": 5}),
        (DriverColumn("a", "timestamptz"), {"type": ["string", "null"], "format": "date-time"}),
        (DriverColumn("a", "timestamp"), {"type": ["string", "null"]}),
        (DriverColumn("a", "jsonb"), {}),
        (DriverColumn("a", "int4", array=True),
         {"type": ["array", "null"], "items": {"type": ["integer", "null"]}}),
        (DriverColumn("a", "varchar", array=True, length=5),
         {"type": ["array", "null"], "items": {"type": ["string", "null"]}}),
        (DriverColumn("a", "interval"), None),
        (DriverColumn("a", None, shown="geometry"), None),
    ],
)  # fmt: skip
def test_the_driver_type_gives_a_json_type(
    column: DriverColumn, schema: dict[str, object] | None
) -> None:
    assert driver_schema(column) == schema


def test_a_column_named_twice_is_refused() -> None:
    with refused("gives the column id twice"):
        record_schema([DriverColumn("id", "int4"), DriverColumn("id", "int8")], {})


def test_a_correction_of_a_column_not_in_the_result_is_refused() -> None:
    with refused(r"the column tota is not in the result \(columns: total\)"):
        record_schema([DriverColumn("total", "numeric")], {"tota": {"type": "string"}})


def test_an_unknown_type_names_what_the_database_calls_it() -> None:
    with refused("column zone: the PostgreSQL type geometry has no JSON type"):
        record_schema([DriverColumn("zone", None, shown="geometry")], {})


def test_an_empty_correction_replaces_the_type() -> None:
    schema = record_schema([DriverColumn("zone", None, shown="geometry")], {"zone": {}})
    assert schema == {"type": "object", "properties": {"zone": {}}}


@pytest.mark.parametrize(
    ("code", "charset", "flags", "length", "name"),
    [
        (1, 63, 0, 4, "tinyint"),
        (1, 63, 0, 1, "bool"),
        (8, 63, 32, 20, "bigint"),
        (246, 63, 0, 12, "decimal"),
        (253, 224, 0, 20, "varchar"),
        (253, 63, 128, 4, "varbinary"),
        (252, 224, 16, 65535, "text"),
        (252, 63, 144, 65535, "blob"),
        (254, 224, 256, 4, "enum"),
        (254, 224, 2048, 12, "set"),
        (245, 63, 0, 0, "json"),
        (16, 63, 32, 3, "bit"),
        (99, 63, 0, 0, None),
    ],
)
def test_the_mysql_protocol_names_the_type(
    code: int, charset: int, flags: int, length: int, name: str | None
) -> None:
    assert mysql_type(code, charset, flags, length) == name


@pytest.mark.parametrize(
    ("type_name", "schema"),
    [
        ("bool", {"type": ["boolean", "null"]}),
        ("year", {"type": ["integer", "null"]}),
        ("decimal", {"type": ["number", "null"]}),
        ("enum", {"type": ["string", "null"]}),
        ("datetime", {"type": ["string", "null"]}),
        ("json", {}),
        ("blob", None),
    ],
)
def test_a_mysql_type_gets_a_json_type(type_name: str, schema: dict[str, object] | None) -> None:
    assert driver_schema(DriverColumn("a", type_name, database=Kind.MYSQL)) == schema
