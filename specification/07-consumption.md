# 07 — Consumption

Principle: **the same number, the same way, in any tool.** Consumption reuses the production
definitions (contracts, rules, metrics) instead of each analyst recomputing them.

Corollary: **reading needs nothing that producing needs.** Someone outside the data team installs only
`statera` (no dbt, no Copier, no legacy-code readers; 01, "Who installs what") and reads from a shared
engine; they never run the process to get the data.

## Where the data is read from

Production writes to the local engine (`[engine]`, built by `statera run`). Those who only read use a
**shared engine** that the data team keeps up to date, declared in `statera.toml`:

```toml
[consumption]
engine = "s3://labor-stats/warehouse.duckdb"      # a DuckDB file: a path (network share), https://, s3://
# engine = "postgres://reader@db.example.org/stats"   # or any engine Ibis connects to
# engine = "athena://?s3_staging_dir=s3://results/statera/&region_name=us-east-1&work_group=analysts"
```

The engine read is, in order: the explicit choice (`catalog.open(engine=…)`, `--engine` in `statera query`,
`metric` and `recipes`), the `STATERA_ENGINE` variable (for a URL with credentials, which **must not** go
into git), `[consumption] engine`, and the local engine. `local` forces the local engine. A DuckDB file is
attached **read-only**; in the other engines, the reader's login and the generated grants are the
boundary. Every output records which engine it read (`engine` in the provenance). With no local engine
and no shared one, reading fails saying whom to ask; `statera doctor` reports it too.

### Connectors: other sources are an extension

Statera reads every engine through one interface, an Ibis backend (`ibis.BaseBackend`): what the
catalog, the queries and the metrics use. It opens by itself a DuckDB file and, through `ibis.connect`,
every engine Ibis has a backend for (PostgreSQL, Trino, MySQL, SQLite, Snowflake, BigQuery, Amazon
Athena…; each with its Ibis backend installed, e.g. `statera[athena]`). Any other source (MariaDB under its
own scheme, H2, Parquet files in a bucket, an internal API…) is **not** Statera's: whoever uses Statera adds
a **connector**, a function that receives the URL and the project and returns an Ibis backend:

```python
# content/connectors/parquet_s3.py: Parquet files in a bucket, <prefix>/<schema>/<table>/*.parquet
from urllib.parse import urlsplit

import ibis
from statera import contracts


def connect(url, project):
    parts = urlsplit(url)  # parquet+s3://bucket/prefix
    con = ibis.duckdb.connect()
    con.raw_sql("CREATE SECRET (TYPE s3, PROVIDER credential_chain)")
    for table in contracts.by_table(project):  # every table with a contract: gold.x, ref.y…
        schema, name = table.split(".")
        if schema not in ("silver", "gold", "ref"):  # what queries may read
            continue
        files = f"s3://{parts.netloc}{parts.path}/{schema}/{name}/*.parquet"
        con.raw_sql(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        con.raw_sql(f"CREATE VIEW {table} AS SELECT * FROM read_parquet('{files}')")
    return con
```

The connector is chosen by the URL's **scheme**, from, in order:

1. the **repository**, in `statera.toml`, as a `file.py:function` of the content (validation checks that
   the function exists, without running it):

   ```toml
   [consumption.connectors]
   parquet+s3 = "connectors/parquet_s3.py:connect"
   ```

2. an **installed package**, by its entry points in the group `statera.connectors` (one package serves
   every repository of an organization):

   ```toml
   [project.entry-points."statera.connectors"]
   h2 = "acme_connectors.h2:connect"
   ```

3. Statera's own behavior (DuckDB file, else `ibis.connect`).

A repository connector overrides a package's, and both override the built-in behavior for their scheme.
The connector **must** open the source read-only, and **must not** take credentials from the URL
written in `statera.toml` (use `STATERA_ENGINE` or the source's own credential chain). `statera doctor`
shows which connector opened the engine.

**Published editions** are read from the local publication folder or, when it is not there, from the
publication storage declared in the series (`files_url` in `series.yaml`, with `{period}` and optionally
`{product}`). By itself, Statera reads a network path, `file://` and `https://`; any other storage (S3, GCS,
Azure Blob, an internal service…) is a **reader**, an extension in the same way as the connectors: a function
`read(url, project) -> bytes`, chosen by the URL's scheme, from the repository (`[consumption.readers]` in
`statera.toml`) or from an installed package (entry points `statera.readers`):

```python
# content/readers/s3.py, declared as: [consumption.readers] s3 = "readers/s3.py:read"
from urllib.parse import urlsplit

import boto3


def read(url, project):
    parts = urlsplit(url)
    return boto3.client("s3").get_object(Bucket=parts.netloc, Key=parts.path.lstrip("/"))["Body"].read()
```

Whatever reads the file, it is checked against the hash in the editions register, so a file changed after
publication is refused.

Partitioned tables (cuts of internal products, 09): the released cuts are recorded where the data is
produced. Read from a shared engine, the cut **must** be given explicitly.

## Recurring queries

```
queries/<theme>/<slug>/  or  products/<p>/queries/<slug>/
├── query.yaml        schema `query`: id in the namespace, the five catalog axes (theme, product,
│                     bases, frequency, purpose), typed parameters, rules, limitations,
│                     output classification (with disclosure control), owner, status, version
├── <slug>.sql        dbt analysis (file name = slug with underscores): ref() and var('<parameter>', default)
└── test.yaml         schema `query-test`: synthetic inputs per ref, parameters, expected output
```

- Parameter types: `period`, `date`, `cut`, `ibge-code`, `uf`, `integer`, `short-text`. Values
  are validated by type **before** reaching the SQL; an undeclared parameter is rejected.
- Queries only read silver, gold and ref (never bronze or staging).
- Running a query needs no dbt: `ref()` is resolved by the same rules the generated `dbt_project.yml`
  gives dbt (schema by layer folder, seeds in `ref`, name = file name). Validation **must** fail when a
  model read by a query overrides its schema or alias, so that both resolutions never diverge.
- The output goes through the disclosure control of `output_classification`; when written to a file,
  it goes out with a provenance Data Package.
- Status (`status`): `exploratory` → `cataloged` → `certified` → `deprecated`.
- `authors` (optional, `name` and `unit`): credit to people, beyond the `owner` role, including those
  outside the data team. It goes into the output as `contributors` (role `author`) of the Data Package.

## Metrics (`<unit>/5-process/metrics.yaml`)

Format inspired by the dbt semantic layer (MetricFlow): `simple` (`aggregation` of an `expression`,
with the possible `sources` and the `dimensions` each source allows) and `ratio` (`numerator` and
`denominator`, times a `factor`). Aggregations: `sum`, `average`, `count`, `min`, `max`. The framework
picks the first source that covers the requested dimensions. Filters go as engine parameters.

## Recipes (`recipes/`)

`.py` files in cells (`# %%`) or `.sql`, with a `Recipe:`, `Layers:`, `Classification:` header.
They **must** be run in CI against synthetic data.

## Library

`statera.consume.catalog` (Python, on top of Ibis) with three read modes and provenance in every output:

| Mode | Question | Source |
|---|---|---|
| current | "What is the latest data?" | silver and gold in the engine |
| cut-off date | "What did it look like in the July 20 extraction?" | bronze partition; silver/gold only if the engine keeps that date |
| published edition | "What number was published?" | the edition's file, with the Data Package hash verified |

The library **must** show the classification and warn when reading what is not broad; it is not a
security boundary. An R library (dbplyr) would follow the same logic.

## Contribution and authorship

Consumption evolves with contributions from people outside the data team (InnerSource model):

- **Entry points without git:** issues "New query proposal" and "Contribute an analysis, a
  calculation or a chart"; curation turns them into a pull request.
- **Destinations:** catalogued query (recurring calculation), recipe (executable step by step) or
  declared visualization (`exposure` with `meta.visualization`, 04).
- **Authorship:** `authors` in the query, `Authors:` in the recipe header and `meta.authors` in the
  visualization, published only with consent.
- **Browsable catalog:** `statera docs --interactive` generates `dbt docs` as a single page, with
  search, lineage and each table's `meta` (product, contract, URN, classification), generated from
  the contracts.
