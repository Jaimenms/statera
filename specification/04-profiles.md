# 04 — Standards profiles

## ODCS v3.1 (contracts)

Beyond the official schema, every contract **must** have in `customProperties`:

| Property | Value |
|---|---|
| `layer` | `bronze` (source-aligned product, phase 4) · `silver` (aggregate product, phase 5) · `gold` (consumer-aligned product, phase 5) · `ref` (reference table). CI checks it against the product's alignment and the phase (09) |
| `urn` | Identifier in the namespace (02), equal to the computed one |
| `classification` | Four-dimension object (03); schema `classification-asset` |
| `dbtSource` / `dbtModel` / `dbtSeed` | dbt node that materializes the table |
| `manualProduction` (instead of the dbt node) | How the table is produced today, outside the process (e.g. a tab of a hand-built spreadsheet). Accepted only up to level 1 (08); the contract's server points to the published file (`type: custom`, `location`, `format`) |
| `extraction` (bronze) | `{source, receivedAtColumn, sourceSystem}`: where and how the framework extracts, in incremental batches (09) |
| `partitionedBy` (aggregate silver) | Cut column (the `cut_date_column` of `[conventions]`, e.g. `cut_date`): the table keeps one set per cut (09) |
| `temporality` (ref) | `stable` · `validity` · `versioned` (09); optional `temporalNote` with the policy |
| `authority` (ref) | Who is the authority for the reference table (the national statistics office, a classification committee, a ministry…) |

Columns: `classification` (sharing: `broad` · `restricted` · `specific`) mandatory; `tags: [personal-data]` when applicable;
`relationships` (ODCS 3.1) for foreign keys to reference tables, which become dbt tests.
Quality: `quality` with metrics from the ODCS library (`rowCount`, `nullValues`, `invalidValues`…)
or SQL; checked by the framework at every edition.

## ODPS v1.0 (products)

Product `customProperties`: `alignment` (`source` · `aggregate` · `consumer`) and `visibility`
(`internal` · `public`), 09; `authorizedConsumers` (internal product, 09); `theme`, `frequency`
(`monthly` · `quarterly` · `semiannual` · `annual` · `biweekly` · `on-demand`), `purpose`
(`official-release` · `on-request` · `study` · `dashboard` · `internal-control`), `bases` (the datasets the
product is built on, in consumer-aligned products), `urn`, `process` (path to the to-be),
`conformanceLevel` (target, 08) and `legacyParity` (`done` · `in-progress` · `not-applicable` ·
`not-declared`); `sourceSystems` (source-aligned product); `distributions` (dissemination product
without a series): the list of files published today, in the DCAT vocabulary, each with `title`,
`format`, `url`, `updated` and, optionally, the `ports` it contains. The `outputPorts` point to
contracts **of the unit itself** (port `customProperties`: optional `spreadsheetTab`;
`inSpreadsheet: false` leaves the port out of the xlsx, like microdata); the `inputPorts`, to contracts
of internal products and reference tables, and they define the dependencies between products (09).

## dbt `exposures` (consumers and visualizations)

Every consumer of gold tables is an `exposure`, with `meta.product` and `meta.sharing`. The
visualizations the public opens also declare `meta.visualization`:

| Field | Value |
|---|---|
| `visualization` | `dashboard` (BI: Power BI, Superset, Metabase…) · `chart` (static images on the portal) |
| `url` (native exposure field) | Where the public opens the visualization |
| `tool` | Tool or format |
| `reads` | `published-edition` · `cut-date` · `current` (the three read modes, 07). A visualization with `sharing: broad` **must** read `published-edition`, to show the same number as the spreadsheet |
| `updated` | When it is updated |
| `authors` (optional) | Credit to people: `name` and `unit` (07) |
| `charts` (`chart` only) | For each image: `title`, `port` (the output port with the chart's numbers as a table) and `alt_text` (WCAG 1.1.1; in Brazil, also e-MAG and Law 13,146/2015, art. 63) |

The generated catalog shows each product's distributions and visualizations under "How to access",
and the lineage links each visualization to its address.

## Frictionless Data Package 2.0 (editions and query outputs)

`x-statera` extension in the package: `product`, `urn`, `period`, `cutDate`, `codeVersion`,
`run` (OpenLineage run), `cuts` (cut and run of each internal product read), `extractions`
(source, system, cut-off date, batch, rows, hash), `references` (version, temporality, hash; 09), `series`,
`corrects` and `synthetic`. In each resource: `contract`, `urn`, `contractVersion`, `classification`,
`suppressedCells`. Query outputs carry the query, the version, the parameters and the SQL hash.

## OpenLineage 2-0-2

Run facet `statera_edition` with `product`, `period` and `cutDate`; the edition job
(`<product>.edition`) as parent of the task jobs (`<product>.<Task_…>`) and of the dbt models
(`dbt.<model>`). Events validated against the official schema before being written.
