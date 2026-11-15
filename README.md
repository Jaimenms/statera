<p align="center">
  <img src="assets/statera-logo.jpg" alt="Statera logo: a Roman steelyard balance. Standard and framework for data product repositories." width="560">
</p>

# Statera

**Statera** is a standard and a framework for data product repositories, designed for statistical
products: data, rules, processes and publications organized as data products, with contracts,
information classification, an executable process, statistical disclosure control and a register of
every published edition.

> *Statera* is the Roman steelyard: a measuring instrument with a calibrated, public weight.

The framework is **generic**: it knows no product. Everything that belongs to an organization (products,
reference tables, queries, recipes, governance) lives in the organization's **data repository**, which
declares Statera as a dependency. An improvement to the framework reaches everyone through a version
upgrade.

```
SPECIFICATION   specification/               formats, profiles, namespace, classification, levels, cuts
     │ implemented by
FRAMEWORK       src/statera/                 Python package + CLI · dbt package · CI workflow · templates (Copier)
     │ used by
DATA            <org>/content/               products · references · queries · recipes · governance
REPOSITORY      <org>/publications/          register of published editions
```

## Installing

Requires [uv](https://docs.astral.sh/uv/) and read access to this repository. Works the same on Windows,
Linux and Mac. What you install depends on what you do:

| You… | Install | Gets you |
|---|---|---|
| only **read** the data (analysts, other teams) | `statera` | catalog, queries, metrics, recipes, published editions |
| **produce** it (the data team) | `statera[produce]` | dbt (the process) and Copier (`init`, `new-product`) |
| keep products with legacy **R** code | `statera[legacy-r]` | reading and writing `.rds` (R itself is installed apart) |
| keep products with legacy **SPSS** code | `statera[legacy-spss]` | reading and writing `.sav` (GNU PSPP is installed apart) |
| read a shared PostgreSQL, Trino or Amazon Athena engine | `statera[postgres]`, `statera[trino]`, `statera[athena]` | the Ibis backend |
| want to **ask in plain language** instead of remembering commands | `statera[copilot]` | `statera copilot`, with a model that runs on your computer ([Ollama](https://ollama.com), installed apart) |

In a data repository you rarely choose: its `pyproject.toml` lists only what reading needs, and the
production tools as dependency groups (`produce`, `legacy-r`, `legacy-spss`) that the commands which produce
install by themselves. Someone who clones the repository to query it never downloads dbt. The copilot is a
group too (`copilot`), installed the first time someone runs `statera copilot`.

The global command (optional) is installed once per computer; the data team, who creates repositories,
installs it with the production tools:

```bash
uv tool install git+ssh://git@github.com/Jaimenms/statera.git                              # to read
uv tool install "statera[produce] @ git+ssh://git@github.com/Jaimenms/statera.git"         # to produce
uv tool upgrade statera                                                                     # later, rarely needed
```

The global `statera` command does almost nothing by itself: inside a data repository it finds the
repository root and hands the command off to the Statera version that repository pins
(`uv run --project <root> statera …`; uv updates the environment when needed, adding the production groups
only for the commands that produce). Every repository keeps its own version, and a computer never runs old
rules by accident. Outside a repository (`statera init`) it runs itself. `uv run statera …` works too, with
nothing installed globally; CI uses that form (`uv run --all-groups statera check --all`).

## Only reading the data

Inside a data repository (no production tools needed):

```bash
statera doctor                                   # says where the data is read from, or whom to ask
statera query list
statera query run uf-series --param uf=SP --output result/
```

The data comes from the shared engine the data team declares in `statera.toml` (`[consumption] engine`);
`STATERA_ENGINE` overrides it, for example with credentials. Statera opens a DuckDB file (on a share,
https:// or s3://) and any engine Ibis knows (PostgreSQL, Trino, Amazon Athena, MySQL, Snowflake…).
Any other source (MariaDB, H2, Parquet in a bucket, an internal API…) is an extension of the organization,
not of Statera: a **connector**, a function that receives the URL and returns an Ibis backend, declared in
`statera.toml` (`[consumption.connectors]`) or shipped in a package (entry point `statera.connectors`).
Published editions are read from the series' publication storage (`files_url`: a share or https:// by
itself; S3 and others through a **reader**, the same kind of extension), with every file checked against
the hash in the register. See [specification 07](specification/07-consumption.md).

## Asking in plain language (optional)

The command line grew with the standard. `statera copilot` is a way in for someone who knows the question
and not the command: a language model **running on your own computer** looks up the repository's catalog
and answers with the command to run, or, when nothing cataloged answers, with the SQL.

```bash
brew install ollama && ollama serve          # once: https://ollama.com/download (Windows: the installer)
ollama pull qwen2.5-coder:7b                 # once: the model, 4.7 GB
statera copilot "how many records did São Paulo have in 2026?"
statera copilot                              # a conversation: "and by month?", "now for Minas Gerais"
```

```
$ statera copilot "how many records did São Paulo have in 2026?"
  · choose: query:uf-series
  · query: {"uf": "SP"}
      ✘ it does not return what was asked (the query returns a monthly series for a UF; it has no year…)
  · choose: table:gold.bar_uf_monthly

  SELECT
    SUM(records) AS total_records
  FROM gold.bar_uf_monthly
  WHERE
    uf_abbr = 'SP' AND period LIKE '2026-%'
  · assumed: 2026 is every reference period starting with '2026-'
  · assumed: São Paulo is 'SP' in the uf_abbr column
  · reads: gold.bar_uf_monthly (ad hoc SQL: no cataloged query or metric answers this)
  Run it? [Y/n, e: edit, ?: explain]

 total_records
           412
```

- **Nothing leaves the computer.** The model is served by Ollama on `localhost`; Statera has no client for a
  hosted model and refuses an address that is not local.
- **What it proposes is checked, not trusted.** A command must be accepted by the real command line (the
  query, the metric and the product exist, the parameters are valid); SQL must be one read-only `SELECT` over
  cataloged tables you may read, accepted by the engine, filtering by values the data has. What fails goes
  back to the model, not to you.
- **It cannot name what does not exist.** The model chooses among the repository's own queries, metrics,
  tables and commands, and fills in only the parameters and options they declare.
- **It prefers what is cataloged**: a recurring query (reviewed, tested, with disclosure control) or an
  official metric when one returns exactly what was asked, SQL of its own otherwise.
- **It asks before running.** What only reads runs after a yes (`--yes` gives it in advance); what produces
  or changes asks every time; what records a person's decision (`resume`, `--approve`) or deletes (`clean`,
  `demo`) it shows and never runs.
- **You may ask what it does, or change it.** At that question, `?` explains the proposal part by part (each
  option, each filter) and `e` lets you edit it (↑ brings the line). What you write goes through the same
  checks, and an edit does not change who may run it.

It is a small model: it gets things wrong, which is why it shows what it assumed and what it read. A larger
local model answers better (`--model`, or `[copilot] model` in `statera.toml`). See
[specification 07](specification/07-consumption.md#copilot-optional).

## Starting a data repository

```bash
statera init my-org --name example-stats --namespace org.example --organization "Example statistics"
cd my-org
statera doctor      # checks the computer and the repository, and says how to fix each problem
statera check       # validation, lint and fast tests: the repository starts out valid
```

Then create the products with `statera new-product <name>`. The README generated in the repository
explains the day-to-day workflow; in VS Code the same commands are available as tasks.

## Example

[`examples/foo-bar/`](examples/foo-bar/content/README.md) is the smallest data repository that goes
through the whole cycle: a source-aligned product (`foo`), a dissemination product in SQL and pandas
(`bar`) and one computed by legacy SPSS and R code (`baz`).

```bash
cd examples/foo-bar
uv run statera demo
```

## What the framework does

| GSBPM phase | Module | What |
|---|---|---|
| 1 | `project`, `contracts`, `classification`, `governance` | Reads `statera.toml`, discovers the products; ODCS contracts; classification; generated CODEOWNERS |
| 2 | `design` | One process per product, in YAML mirroring BPMN 2.0 (`2-design/process.yaml`); generated `.bpmn`, SVG and viewer |
| 4 | `collect` | Incremental extraction in batches; immutable cuts with a manifest |
| 5 | `process` | dbt project generated from the products; rules with cases (SQL and Python); legacy code (SPSS via PSPP, R) |
| 6 | `analyse` | Contracts checked against the engine; controls; statistical disclosure control (cell suppression, k-anonymity) |
| 7 | `disseminate` | Edition package (CSV, xlsx, summary, Data Package, CKAN) and editions register |
| 8 | `evaluate` | Quality indicators and action plan; alerts acknowledged before approval, or blocking |
| — | `extend` | The public extension API: `Task`, `Check`, `Await` and `SyntheticSource`, the classes every process step extends (the framework's and the products') |
| — | `consume` | Library (Ibis): catalog, published editions, cuts, metrics, queries, recipes |
| — | `copilot` | Optional: plain-language questions answered with a checked command or SQL, by a local model |

## Command line

`statera init | doctor | check | new-product | sync | validate | conformance | run | resume | status |
cuts | synthetic | demo | query | metric | recipes | rules | contracts | lineage | evaluate | docs [--interactive] |
clean | copilot`. See `statera --help`. Commands work from any folder of a data repository.

`statera status` shows, for every product, where its latest edition (or cut) is in the BPMN process,
the role that has to act and the evaluation alerts; `--product x` lists every edition of one product,
and `--product x --period y` details one edition.

## Library

```python
from statera.consume import catalog

cat = catalog.open()  # the shared engine of statera.toml; engine="local" reads your own runs
cat.search(theme="formal-employment", layer="gold")
t = cat.table("gold.employment_flows_uf_monthly")  # Ibis table (current)
ed = cat.edition("employment-flows", "2026-06")  # what was published (local or files_url), hash verified
ed.table("net-change-by-uf")
ed.provenance
```

## Extending

A process names its steps by class: the framework's (`Extract`, `DbtBuild`, `Gate`, `Package`, `Publish`,
`Evaluate`, `MarkCut`, `PinCut`) or the product's own, written against `statera.extend` (stable from 0.6.0,
specification 05):

```python
from statera.extend import Check, CheckContext, CheckResult


class NoRecordsLost(Check):  # products/bar/6-analyse/checks.py, with cases in check-cases/NoRecordsLost.yaml
    """For every key released in the previous edition, the number of records must not decrease."""

    def evaluate(self, ctx: CheckContext) -> CheckResult: ...
```

`Task` and `Await` classes go in `<phase>/tasks.py`, `Check` classes in `6-analyse/checks.py` (listed in
`controls.yaml` and run by a `Gate`) and `SyntheticSource` classes in `3-build/synthetic.py`
(`statera synthetic`). Validation checks what the classes declare (GSBPM sub-process, tables, outcomes)
against the process and the contracts without running them.

## Development

```bash
uv sync && uv run pytest -q && uv run ruff check .   # the dev group includes every extra
```

The framework tests depend on no organization. CI also runs the `foo-bar` example end to end and, where
available, the SPSS syntax (GNU PSPP) and the R scripts.

## Specification

[specification/](specification/README.md): file formats, ODCS and ODPS profiles, namespace,
classification, processes, controls, consumption, conformance levels and internal products.
