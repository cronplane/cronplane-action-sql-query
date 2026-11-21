"""Reads the result of a SQL query into the stream `records`, all of it or
from where the last successful run stopped."""

from __future__ import annotations

import sys

from cronplane_actions import Action, ActionError, JsonValue

from sql_query import corrections
from sql_query.columns import record_schema
from sql_query.database import Database
from sql_query.errors import Rejected, SqlQueryError
from sql_query.flow_files import one_file
from sql_query.incremental import Mark, Plan, advanced, check_columns, last_mark, wrap
from sql_query.query import Query, read_source
from sql_query.reader import read
from sql_query.values import decimal_text, to_json

STREAM = "records"


def _allowed_address(action: Action, database: Database) -> str:
    if action.network.allowed is None:
        raise SqlQueryError(
            "the step gives no allowed hosts; add to the step:\n"
            f'  network: {{allow: ["{database.allow_line}"]}}'
        )
    return str(action.network.resolve(database.host, database.port)[0])


def _plan(action: Action) -> Plan:
    inputs = action.inputs
    return Plan(
        cursor=inputs.string("cursor") if inputs.has("cursor") else None,
        key=tuple(str(k) for k in inputs.items("key")) if inputs.has("key") else (),
        start=inputs.string("start") if inputs.has("start") else None,
        lookback=inputs.integer("lookback"),
    )


def _query(action: Action) -> Query:
    query_file = one_file(action.environ, "query", (".sql",))
    if query_file is None:
        raise SqlQueryError("flow_files.query: the step gives no query")
    params = {name: str(value) for name, value in action.inputs.entries("params").items()}
    return Query.build(read_source(query_file), params)


def run(action: Action) -> None:
    database = Database.parse(action.secret("DATABASE_URL"))
    if database.url.password:
        action.mask(database.url.password)
    address = _allowed_address(action, database)
    plan = _plan(action)
    step = action.streams.step
    mark = last_mark(plan, action.state.stream(STREAM), step) if plan.cursor else None
    query = wrap(_query(action), plan, mark)
    types_file = one_file(action.environ, "types", (".yaml", ".yml", ".json"))
    columns_fixed = corrections.read(types_file) if types_file else {}

    with read(
        database,
        address,
        query,
        timeout=action.inputs.integer("timeout"),
        batch_size=action.inputs.integer("batch_size"),
    ) as result:
        check_columns(plan, result.columns)
        schema = record_schema(result.columns, columns_fixed)
        names = [c.name for c in result.columns]
        texts = [name in columns_fixed and decimal_text(columns_fixed[name]) for name in names]
        writer = action.streams.writer(
            STREAM,
            schema,
            key_properties=plan.key,
            bookmark_properties=[plan.cursor] if plan.cursor else [],
            max_rejected=action.inputs.integer("max_rejected"),
        )
        with writer:
            for row in result.rows:
                record: dict[str, JsonValue] = {}
                problem = None
                for name, text, value in zip(names, texts, row, strict=True):
                    try:
                        record[name] = to_json(value, decimal_text=text)
                    except Rejected as e:
                        record[name] = str(value)
                        problem = problem or f"{name}: {e}"
                if plan.cursor and record[plan.cursor] is None:
                    problem = problem or "cursor is null"
                if problem is not None:
                    writer.reject(record, problem)
                    continue
                written = writer.written
                writer.write(record)
                # A record the schema rejects does not move the cursor.
                if plan.cursor and writer.written > written:
                    read_mark = Mark(record[plan.cursor], [record[k] for k in plan.key])
                    mark = advanced(plan, mark, read_mark)
            action.outputs.set("rows", writer.written)
            action.outputs.set("rejected", writer.rejected)
        if mark is not None:
            action.state.set_stream(
                STREAM, {"cursor": mark.cursor, "last_key": mark.key, "columns": plan.columns}
            )


def main() -> None:
    try:
        run(Action.from_environment())
    except (ActionError, SqlQueryError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
