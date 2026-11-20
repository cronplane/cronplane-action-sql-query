# sql-query

Reads the result of a SQL query into the stream `records`, from PostgreSQL. The query is a file of the flow; the action reads it in a read-only transaction and writes one record per row, with a `schema.json` that the next steps check each record against.

```yaml
steps:
  - id: extract
    uses: hub/sql-query@0.1
    with: {params: {country: "${inputs.country}"}}
    flow_files: {query: ["sql/customers.sql"], types: ["types/customers.yaml"]}
    secrets: {DATABASE_URL: crm_db}
    network: {allow: ["db.acme.internal:5432"]}

  - id: load
    uses: hub/hubspot-upsert@0.1
    needs: [extract]
    streams: {consumes: {contacts: extract.records}}
```

```sql
-- sql/customers.sql
SELECT c.id, c.email, c.updated_at, sum(o.amount) AS total
FROM customers c LEFT JOIN orders o ON o.customer_id = c.id
WHERE c.country = :country
GROUP BY c.id
```

## Inputs

| Input | Type | Use |
|---|---|---|
| `params` | map of strings, untrusted | The values of the query's parameters (`:country`). |
| `timeout` | integer, 600 | Seconds each statement may take. |
| `batch_size` | integer, 10000 | Rows fetched at a time. |
| `max_rejected` | integer, 0 | Rows the step may reject before it fails. |

`params` is untrusted: a value can come from whoever starts a run. The action only binds it as a parameter of the query; it never writes it into the query. The query and `params` must name the same parameters, else the step fails and lists both. A colon in a literal of the query is written `\:` (`'{"a"\:1}'`).

## Secrets

| Secret | Use |
|---|---|
| `DATABASE_URL` | `postgresql+psycopg://user:password@host:port/database`. Options: `sslmode`, `sslrootcert=system`, `application_name`; any other option is refused. The password is masked. Use an account that can only read. |

## Flow files

| Set | Use |
|---|---|
| `query` | One `.sql` file: the query. Required. |
| `types` | One `.yaml` or `.json` file: corrections of the column types. Optional. |

## Outputs and stream

| Output | Type |
|---|---|
| `rows` | integer: the records written |
| `rejected` | integer: the rows rejected |

The stream `records` holds one record per row. The type of each column comes from the driver:

| PostgreSQL | JSON Schema (each with `"null"`) |
|---|---|
| `int2`, `int4`, `int8` | `integer` |
| `numeric`, `float4`, `float8` | `number` (exact to 15 significant digits) |
| `bool` | `boolean` |
| `text`, `varchar(n)`, `char(n)`, `uuid` | `string` (`maxLength: n`) |
| `timestamptz` | `string`, `format: date-time` |
| `timestamp`, `date`, `time`, `timetz` | `string` |
| `json`, `jsonb` | any value |
| `<type>[]` | `array` of that type |

Another type stops the step: cast it in the query (`zone::text AS zone`), or give its type in the types file. A value with no JSON form (`NaN`) rejects its row.

## Types file

The types file gives the schema of some columns; the others keep the type of the driver. It uses the keywords of JSON Schema that the libraries read: `type`, `properties`, `required`, `additionalProperties`, `items`, `minItems`, `maxItems`, `enum`, `minLength`, `maxLength`, `minimum`, `maximum`, `format: date-time`.

```yaml
columns:
  email: {type: string, maxLength: 255}            # never NULL: no "null" in type
  siret: {type: [string, "null"], minLength: 14, maxLength: 14}
  total: {type: [string, "null"]}                  # more than 15 digits
```

It is read as YAML 1.2. An unquoted `yes`, `no`, `on` or `off` is refused (YAML 1.1 reads it as a boolean): quote it. A column of the file that the query does not give stops the step.

A row that does not fit the schema is rejected: it goes to `rejected/records/<step>/` with the reason, and counts in `max_rejected`.

## Network

The step must give `network: {allow: [...]}` with the host and port of the database; without it, the step stops and says the line to add. The action connects only to an address of that host.

## Checking it

```sh
docker compose -f <kit>/fixtures/compose.yaml --profile postgres up -d --wait
export CRONPLANE_BIN=/path/to/cronplane
uv run --project <kit>/python cronplane-kit check .
```

The tests use the PostgreSQL of the kit's fixtures; `SQL_QUERY_POSTGRES` gives another one.
