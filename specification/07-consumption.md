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
queries/<theme>/<slug>/
├── query.yaml        schema `query`: id in the namespace, the five catalog axes (theme, product,
│                     bases, frequency, purpose), typed parameters, rules, limitations,
│                     output classification (with disclosure control), owner, status, version
├── <slug>.sql        dbt analysis (file name = slug with underscores): ref() and var('<parameter>', default)
└── test.yaml         schema `query-test`: synthetic inputs per ref, parameters, expected output
```

- Every recurring query lives in `queries/<theme>/<slug>/`, including the ones that serve a single
  product: the `product` axis says which (the lineage and the documentation group it with that product;
  `governance/shared-owners.yaml` may give its review to the product's team). Since 0.7 a
  `products/<p>/queries/` folder fails validation.
- `statera query show|run <id> --help` explains one query: what it answers, each parameter (type, format,
  required or default), limitations, output classification and commands to copy (Python:
  `statera.consume.queries.describe`). A missing or undeclared parameter points to it.
- `statera query show <id> [--param name=value]` prints the plain SQL that `statera query run` executes:
  `ref()` resolved to the engine's tables and `var()` to the validated parameters (defaults where not
  given), with a header line naming the query, its version and the parameters; `run --output` saves it
  as `query.sql` next to the result (Python: `statera.consume.queries.compiled_sql`).
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

## Copilot (optional)

`statera copilot "<question>"` answers a question in plain language with what to run. It is a convenience
over everything above, not another way to compute a number: it is installed apart (extra and dependency
group `copilot`) and nothing else depends on it.

**The model runs on the person's computer.** It is served by [Ollama](https://ollama.com) on a loopback
address; an address that is not local is refused, and the framework has no client for a hosted model. The
catalog and the values of the data that the model sees therefore never leave the computer.

```toml
[copilot]                            # all optional
model = "qwen2.5-coder:7b"           # any model the local Ollama has (STATERA_COPILOT_MODEL, --model)
url = "http://localhost:11434"       # loopback only (STATERA_COPILOT_URL)
context = 8192                       # context window, in tokens
```

**What it answers with**: a cataloged query (`statera query run`) or an official metric (`statera metric`)
when one returns exactly what was asked; another command (status, run, validation…) when the request is
about operating the repository; and, when nothing cataloged returns what was asked, a SQL query of its own
over the cataloged tables. It may also ask back, or explain what a card says.

**What it knows** is built from the repository at each start, with nothing to index or keep up to date: one
card per command (from the command line itself), per table (its contract: purpose, columns, classification,
the grain of a row), per cataloged query, per metric and per product.

**How it decides**: in small steps, each one held by the model server to a JSON schema built from the
repository, so that a small local model cannot write what does not exist:

1. it **chooses** what answers, among the ids of the cards (a repository too big to list is first searched
   by the words of the question, and the model may ask for another search);
2. it **fills in** only what it chose, seeing only that card: the parameters a query declares, the dimensions
   a metric's sources allow, the options the command has, or the SQL. It also says whether the query really
   returns what was asked (if not, it chooses again without it), and a flag it turned on is kept only when,
   asked again, the request asks for what the flag does;
3. the result is **checked** before a person sees it; what fails goes back to the model with the reason:

| Proposal | Must |
|---|---|
| a command | be accepted by the real command line; name a query, a metric and a product that exist, with parameters valid by their declared type |
| SQL | be one statement, a `SELECT` with nothing that writes and no function that reads files; read only tables with a contract that the role may read (`authorizedConsumers`, as in the catalog); be accepted by the engine (`EXPLAIN`: it binds columns and types without reading the data); filter by values the column has |

To check that last point the copilot looks a value up in its column, and tells the model what the column
holds when the value is not there (the code that goes with a name, for instance). It never looks at a
personal column, and in a table that is not for broad sharing it only shows values that several rows share.

SQL that counts the rows of an aggregated table (`count(*)` where a row is a group) is valid and usually
not what was asked: the model is asked once whether it means it. An answer in SQL names the tables read
and repeats their classification warning, as `cat.table` does; it does **not** go through disclosure
control (as raw SQL in the library does not): a number that will be shared belongs in a cataloged query.

**What it may run**:

| The command… | Examples | Runs |
|---|---|---|
| only reads | `query`, `metric`, `status`, `validate`, SQL (the engine is opened read-only) | after a yes; `--yes` gives it in advance |
| produces or changes | `run`, `sync`, `docs`, `new-product` | after a yes, asked every time; never without a terminal |
| records a person's decision, or deletes | `resume`, `--approve`, `--force`, `clean`, `demo` | never: shown for the person to run |

Where it asks, the person may also reply `?`, to have the proposal explained part by part from the cards
of what it names (the command and its query, metric or product; the tables of a SQL query), or `e`, to
edit it. An edited command or SQL query goes through the checks above and is shown again as what it now
is, under the same table: an edit that turns a query into `resume` is not run. Two of those checks become
cautions in SQL the person wrote, who decides knowing them: a value the data does not have, and the count
of the rows of an aggregated table.

The human tasks of a process stay human: the copilot can say how to approve an edition, never approve it.

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
