"""The types file: the schema of the columns the driver's type does not fit.

YAML 1.2 (JSON is YAML too), read strictly: an unquoted value that YAML 1.1
reads as a boolean (`no`, `on`…) or a date is refused, so that a file means
the same to every reader."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Final

from cronplane_actions import JsonValue
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from ruamel.yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from .errors import SqlQueryError

# Booleans in YAML 1.1 (PyYAML and others), text in YAML 1.2.
AMBIGUOUS: Final = frozenset({"yes", "no", "on", "off"})
MAX_BYTES: Final = 1024 * 1024


def _yaml() -> YAML:
    yaml = YAML(typ="safe", pure=True)
    yaml.version = (1, 2)
    return yaml


def _check_plain(node: Node, where: str) -> None:
    if isinstance(node, ScalarNode):
        if node.style is None and node.value.lower() in AMBIGUOUS:
            line = node.start_mark.line + 1
            raise SqlQueryError(
                f"{where}, line {line}: {node.value} is read differently by YAML readers; "
                f'quote it ("{node.value}"), or write true or false'
            )
    elif isinstance(node, SequenceNode):
        for item in node.value:
            _check_plain(item, where)
    elif isinstance(node, MappingNode):
        for key, value in node.value:
            _check_plain(key, where)
            _check_plain(value, where)


def _json_value(value: object, where: str) -> JsonValue:
    if isinstance(value, float) and not math.isfinite(value):
        raise SqlQueryError(f"{where}: {value} is not a JSON number")
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, list):
        return [_json_value(item, where) for item in value]
    if isinstance(value, dict):
        out: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise SqlQueryError(f"{where}: the key {key!r} is not text; quote it")
            out[key] = _json_value(item, where)
        return out
    raise SqlQueryError(f"{where}: {value!r} is not a JSON value (a date?); quote it")


def _no_constant(name: str) -> object:
    raise json.JSONDecodeError(f"{name} is not a JSON number", name, 0)


def parse(text: str, where: str) -> dict[str, dict[str, JsonValue]]:
    """The schema of each column the file names, under `columns:`."""
    try:
        if where.endswith(".json"):
            document: object = json.loads(text, parse_constant=_no_constant)
        else:
            yaml = _yaml()
            node = yaml.compose(text)
            if node is not None:
                _check_plain(node, where)
            document = yaml.load(text)
    except (json.JSONDecodeError, YAMLError) as e:
        raise SqlQueryError(
            f"{where}: not valid {'JSON' if where.endswith('.json') else 'YAML'}: {e}"
        ) from None
    value = _json_value(document, where)
    if not isinstance(value, dict) or set(value) != {"columns"}:
        raise SqlQueryError(f"{where}: one key is expected, columns: (the schema of each column)")
    columns = value["columns"]
    if not isinstance(columns, dict):
        raise SqlQueryError(f"{where}: columns: maps each column to its schema")
    out: dict[str, dict[str, JsonValue]] = {}
    for name, schema in columns.items():
        if not isinstance(schema, dict):
            raise SqlQueryError(f"{where}: column {name}: a schema is a mapping ({{type: string}})")
        out[name] = schema
    return out


def read(path: Path) -> dict[str, dict[str, JsonValue]]:
    try:
        data = path.read_bytes()
    except OSError as e:
        raise SqlQueryError(f"{path.name}: cannot be read: {e}") from e
    if len(data) > MAX_BYTES:
        raise SqlQueryError(f"{path.name}: {len(data)} bytes, more than {MAX_BYTES}")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise SqlQueryError(f"{path.name}: not UTF-8 text") from None
    return parse(text, path.name)
