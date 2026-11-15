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
```

The engine read is, in order: the explicit choice (`catalog.open(engine=…)`, `--engine` in `statera query`,
`metric` and `recipes`), the `STATERA_ENGINE` variable (for a URL with credentials, which **must not** go
into git), `[consumption] engine`, and the local engine. `local` forces the local engine. A DuckDB file is
attached **read-only**; in the other engines, the reader's login and the generated grants are the
boundary. Every output records which engine it read (`engine` in the provenance). With no local engine
and no shared one, reading fails saying whom to ask; `statera doctor` reports it too.

**Published editions** are read from the local publication folder or, when it is not there, from the
publication storage declared in the series (`files_url` in `series.yaml`, with `{period}`: an https://
URL or a network path). Either way, each file is checked against the hash in the editions register, so a
file changed after publication is refused.

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
