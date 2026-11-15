# BR-BAR-001 — Event reference month

| Field | Value |
|---|---|
| Status | illustrative |
| Layer | gold |
| Implementation | `bar_uf_monthly` (dbt) |
| GSBPM | 5.7 Calculate aggregates |

## Definition

Each record counts in the reference month of the **event date**, not of the reception date. A January record
received in February goes into January, which is why January changes from one edition to the next.
