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
Linux and Mac.

```bash
uv tool install git+ssh://git@github.com/Jaimenms/statera.git   # once per computer
uv tool upgrade statera                                         # later, rarely needed
```

The global `statera` command does almost nothing by itself: inside a data repository it finds the
repository root and hands the command off to the Statera version that repository pins
(`uv run --project <root> statera …`; uv updates the environment when needed). Every repository keeps
its own version, and a computer never runs old rules by accident. Outside a repository (`statera init`)
it runs itself. `uv run statera …` works too, with nothing installed globally; CI uses that form.

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
| 2 | `design` | Process in YAML mirroring BPMN 2.0; generated `.bpmn`, SVG and viewer |
| 4 | `collect` | Incremental extraction in batches; immutable cuts with a manifest |
| 5 | `process` | dbt project generated from the products; rules with cases (SQL and Python); legacy code (SPSS via PSPP, R) |
| 6 | `analyse` | Contracts checked against the engine; controls; statistical disclosure control (cell suppression, k-anonymity) |
| 7 | `disseminate` | Edition package (CSV, xlsx, summary, Data Package, CKAN) and editions register |
| 8 | `evaluate` | Quality indicators and action plan |
| — | `consume` | Library (Ibis): catalog, published editions, cuts, metrics, queries, recipes |

## Command line

`statera init | doctor | check | new-product | sync | validate | conformance | run | resume | status |
cuts | demo | query | metric | recipes | rules | contracts | lineage | evaluate | docs [--interactive] |
clean`. See `statera --help`. Commands work from any folder of a data repository.

## Library

```python
from statera.consume import catalog

cat = catalog.open()
cat.search(theme="formal-employment", layer="gold")
t = cat.table("gold.employment_flows_uf_monthly")  # Ibis table (current)
ed = cat.edition("employment-flows", "2026-06")  # what was published, hash verified
ed.table("net-change-by-uf")
ed.provenance
```

## Development

```bash
uv sync && uv run pytest -q && uv run ruff check .
```

The framework tests depend on no organization. CI also runs the `foo-bar` example end to end and, where
available, the SPSS syntax (GNU PSPP) and the R scripts.

## Specification

[specification/](specification/README.md): file formats, ODCS and ODPS profiles, namespace,
classification, processes, controls, consumption, conformance levels and internal products.
