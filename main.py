"""Reads the result of a SQL query into the stream `records`."""

from __future__ import annotations

import sys

from cronplane_actions import Action, ActionError, JsonValue

from sql_query import corrections
from sql_query.columns import record_schema
from sql_query.database import Database
from sql_query.errors import Rejected, SqlQueryError
from sql_query.flow_files import one_file
from sql_query.query import Query, read_source
from sql_query.reader import read
from sql_query.values import to_json

STREAM = "records"


def _allowed_address(action: Action, database: Database) -> str:
    if action.network.allowed is None:
        raise SqlQueryError(
            "the step gives no allowed hosts; add to the step:\n"
            f'  network: {{allow: ["{database.allow_line}"]}}'
        )
    return str(action.network.resolve(database.host, database.port)[0])


def run(action: Action) -> None:
    url = action.secret("DATABASE_URL")
    database = Database.parse(url)
    if database.url.password:
        action.mask(database.url.password)
    address = _allowed_address(action, database)

    query_file = one_file(action.environ, "query", (".sql",))
    if query_file is None:
        raise SqlQueryError("flow_files.query: the step gives no query")
    params = {name: str(value) for name, value in action.inputs.entries("params").items()}
    query = Query.build(read_source(query_file), params)
    types_file = one_file(action.environ, "types", (".yaml", ".yml", ".json"))
    columns_fixed = corrections.read(types_file) if types_file else {}

    with read(
        database,
        address,
        query,
        timeout=action.inputs.integer("timeout"),
        batch_size=action.inputs.integer("batch_size"),
    ) as result:
        schema = record_schema(result.columns, columns_fixed)
        names = [c.name for c in result.columns]
        writer = action.streams.writer(
            STREAM, schema, max_rejected=action.inputs.integer("max_rejected")
        )
        with writer:
            for row in result.rows:
                record: dict[str, JsonValue] = {}
                problem = None
                for name, value in zip(names, row, strict=True):
                    try:
                        record[name] = to_json(value)
                    except Rejected as e:
                        record[name] = str(value)
                        problem = problem or f"{name}: {e}"
                if problem is None:
                    writer.write(record)
                else:
                    writer.reject(record, problem)
            action.outputs.set("rows", writer.written)
            action.outputs.set("rejected", writer.rejected)


def main() -> None:
    try:
        run(Action.from_environment())
    except (ActionError, SqlQueryError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
