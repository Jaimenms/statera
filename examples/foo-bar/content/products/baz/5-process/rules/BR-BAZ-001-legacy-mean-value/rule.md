# BR-BAZ-001 — Mean value by UF (legacy SPSS script)

| Field | Value |
|---|---|
| Status | illustrative; parity with SPSS in progress |
| Layer | gold |
| Implementation | `baz_uf_monthly.sps` (legacy SPSS), run by GNU PSPP in the Python model `baz_uf_monthly` |
| GSBPM | 5.7 Calculate aggregates |

## Definition

Mean value = total value / number of records, by UF and reference month, rounded to two decimal places.

## Execution

The cases run through the framework (`statera rules test`) and **require GNU PSPP**: where it is not
installed, they are reported as "not run"; the CI installs it and requires them. PSPP is not SPSS: before
the result replaces the SPSS one, equivalence is certified (the same cut in both, cell-by-cell
comparison).
