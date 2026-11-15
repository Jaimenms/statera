# BR-BAZ-002 — Median value by UF (legacy R script)

| Field | Value |
|---|---|
| Status | illustrative; parity with the legacy code in progress |
| Layer | gold |
| Implementation | `baz_uf_monthly_median.R` (legacy R), run by Rscript in the Python model `baz_uf_monthly_median` |
| GSBPM | 5.7 Calculate aggregates |

## Definition

Median value of records by UF and reference month, rounded to two decimal places. With an even number of
records, the mean of the two middle values (like R's `median`).

## Execution

The cases run through the framework (`statera rules test`) and **require R**: where it is not
installed, they are reported as "not run"; the CI installs it and requires them.
