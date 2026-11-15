# statera-foo-bar — minimal content repository example

> **Example with SYNTHETIC DATA and fictitious names.** Use it as a reference when starting a new
> repository following the statera standard (`statera init`).

The smallest repository that still goes through the whole cycle: batch extraction, cut, transformation,
controls, statistical disclosure control, publication and editions register. It also shows the three ways
to compute a product: **SQL** (dbt), **Python** (pandas, dbt Python model) and **legacy code**
(SPSS syntax run by GNU PSPP and an R script run by Rscript, called from a Python model).

```
Foo System ──► foo (internal, source: batches and cuts) ──cut──► bar (dissemination; SQL + pandas)   level 3
                                                    ref_uf ─┤
                                                            └─► baz (dissemination; legacy SPSS and R) level 2
```

| Unit | Purpose | Files |
|---|---|---|
| [products/foo/](products/foo/1-specify/product.odps.yaml) | Extracts the Foo System in immutable batches and releases **cuts** | descriptor, classification, owners, process, [synthetic source](products/foo/3-build/synthetic.py), bronze contract, staging |
| [products/bar/](products/bar/1-specify/product.odps.yaml) | Publishes records and value by UF and month (SQL) and the change over the previous month ([Python model](products/bar/5-process/models/marts/bar/bar_uf_monthly_change.py), pandas) | descriptor, classification, owners, calendar, process, 2 gold contracts, 3 models, 2 rules with cases, 2 controls (one a [product check](products/bar/6-analyse/checks.py) with cases), series, summary, 1 query |
| [products/baz/](products/baz/1-specify/product.odps.yaml) | Mean value by UF, from the [legacy SPSS script](products/baz/5-process/models/marts/baz/baz_uf_monthly.sps) that the [Python model](products/baz/5-process/models/marts/baz/baz_uf_monthly.py) runs in GNU PSPP; and median by UF, from the [legacy R script](products/baz/5-process/models/marts/baz/baz_uf_monthly_median.R) that the [Python model](products/baz/5-process/models/marts/baz/baz_uf_monthly_median.py) runs in Rscript | level 2: no executable process yet; **requires PSPP and R** (`apt install pspp r-base-core`), so it is left out of the demo |
| [references/uf/](references/uf/ref_uf.odcs.yaml) | UFs (IBGE) | CSV and contract |
| [governance/](governance/roles.yaml) | Roles and shared owners | 2 files |
| `statera.toml` | Name, namespace `org.example.foo-bar`, engine, register, demo | 1 file |

The processes name the steps by class (`statera.extend`, specification 05): the framework's `Extract`,
`Gate`, `MarkCut`, `PinCut`, `DbtBuild`, `Package` and `Publish`, and the controls name checks: the
framework's `RevisionBetweenEditions` and bar's own `NoRecordsLost` (`6-analyse/checks.py`, with its cases in
`6-analyse/check-cases/`). foo's `FooSystem` (`3-build/synthetic.py`) is the `SyntheticSource` that stands in
for the Foo System in the demo.

Generated (do not edit by hand): `dbt_project.yml`, `dbt/generated/`, the `.bpmn` files,
`.github/CODEOWNERS` and `docs/`.

## Run

From any folder of `examples/foo-bar/` (with `statera` installed globally, or prefixing `uv run`):

```bash
statera doctor                 # environment, pinned version, legacy tools
statera check                  # validation, lint and tests (what CI runs)
statera demo                   # synthetic data, 2 editions of bar (each first releases the foo cut it needs)
statera synthetic --fault resent-january   # the source sends January twice: bar's control CK-01 blocks 2026-02
                                           # (the correction, as a rule with its case: ../incident/resent-january.patch)
statera conformance            # level of each product and what is missing for the next one
statera rules test             # rule cases in Python models (the baz ones require PSPP and R)
statera docs --interactive     # browsable catalog at content/target/static_index.html
```

Editions are registered in `../publications`.

## Start your own from this one

1. Copy the folder and change the `[project]` table in `statera.toml` (name, acronym, namespace).
2. Replace `foo` with your source system. The bronze contract says where to extract from
   (`customProperties.extraction`), and the reception column defines the cut.
3. Replace `bar` with your dissemination product. To create another product, use
   `uv run statera new-product <name>`: it starts at level 1.
4. Run `uv run statera sync && uv run statera validate`.
