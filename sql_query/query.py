"""The query of the flow and the values of its parameters."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from sqlalchemy import Select, TextClause, text

from .errors import SqlQueryError

#: The prefix of the parameters the action binds itself.
RESERVED: Final = "cronplane_"

MAX_BYTES: Final = 1024 * 1024


@dataclass(frozen=True)
class Query:
    source: str
    clause: TextClause | Select[Any]
    params: dict[str, Any]

    @classmethod
    def build(cls, source: str, params: Mapping[str, str]) -> Query:
        """The query with its parameters bound; an error when the query and
        `params` do not name the same parameters."""
        if not source.strip():
            raise SqlQueryError("the query file is empty")
        reserved = sorted(name for name in params if name.startswith(RESERVED))
        if reserved:
            raise SqlQueryError(
                f"params: {', '.join(reserved)}: a name that starts with {RESERVED} is the action's"
            )
        clause = text(source)
        named = set(clause.compile().params)
        given = set(params)
        if named != given:
            raise SqlQueryError(
                f"the query uses the parameters [{', '.join(sorted(named))}], "
                f"params gives [{', '.join(sorted(given))}]: give each, and only those. "
                r"A colon in a literal of the query is written \: ('{\"a\"\:1}')"
            )
        return cls(source, clause, dict(params))


def read_source(path: Path) -> str:
    try:
        data = path.read_bytes()
    except OSError as e:
        raise SqlQueryError(f"{path.name}: cannot be read: {e}") from e
    if len(data) > MAX_BYTES:
        raise SqlQueryError(f"{path.name}: {len(data)} bytes, more than {MAX_BYTES}")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise SqlQueryError(f"{path.name}: not UTF-8 text") from None
