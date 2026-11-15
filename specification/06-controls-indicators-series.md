# 06 — Controls, indicators and series

## Controls (`6-analyse/controls.yaml`)

The identity controls of the current edition are dbt SQL tests (`6-analyse/dbt-tests/`).
Those that compare with the previous edition or with the history are declared (`controls`, each with
`id`, `name`, `type` and the type's parameters); the **type** belongs to the framework:

| Type | Parameters | Result |
|---|---|---|
| `revision-between-editions` | `port`, `key`, `measures`, `relative_base`, `warning_threshold`, `error_threshold` | Revision per key against the previous published edition; above the error threshold, it blocks |
| `outlier` | `port`, `measure`, `factor`, `severity` (`warning` · `error`) | Value for the reference month against the historical mean |

The revisions table of the first revision control goes into the summary and into the revision
indicator.

## Indicators (`8-evaluate/indicators.yaml`)

Each indicator declares a `calculation`, with no product code:

- `{function: <name>, ...}`: generic framework function (`dissemination_lag`,
  `cut_date_lag`, `max_relative_revision` with `control`, `check_failures`,
  `checks_run`, `open_format_files`, `relative_automated_duration`,
  `modeled_manual_hours`);
- `{sql: ...}`: query on the engine, with `{{ period }}`, `{{ cut_date }}` and
  `{{ published('<port>') }}` (the edition's published file).

Alerts (`alert`, with `operator` and `value`) and the action plan (`action_plan`, GSBPM 8.3) go in the
same file; each indicator's `dimension` is one of the quality dimensions (`relevance`,
`accuracy-and-reliability`, `timeliness-and-punctuality`, `accessibility-and-clarity`,
`coherence-and-comparability`, `efficiency`). The evaluation goes to the editions register together
with the approved edition.

## Series (`7-disseminate/series.yaml`)

They describe what **every** edition publishes: file names (`files_per_edition`, with `{port}` and
`{period}`), `license`, destination on the open data portal (`open_data_portal`, e.g. a CKAN instance), `main_resource` (the
port that summarizes the edition: its hash goes into the index), revision and correction policies
(`revision_policy`, `correction_policy`) and the path in the editions register (`edition_registry`).
The summary is a Jinja2 template (`summary.md.j2`) that receives the published tables
(`tables[<port>]`), the edition (`edition`), the product (`product`), the revisions (`revisions`) and
the disclosure controls (`controls`), with formatting filters (`thousands` thousands separator, `signed`
explicit sign, `pct` percentage, `brl` Brazilian real currency) and `in_period` (the rows for the edition's
reference month), and the functions `top(rows, column, n)` and `bottom(rows, column, n)` (the n rows with
the largest or smallest values).
