# BR-BAR-002 — Change over the previous month

| Field | Value |
|---|---|
| Status | illustrative |
| Layer | gold |
| Implementation | `bar_uf_monthly_change` (dbt **Python** model, pandas) |
| GSBPM | 5.7 Calculate aggregates |

## Definition

The change for a UF in a reference month is `(records / records in the previous month − 1) × 100`, rounded to
two decimal places. In the UF's first reference month there is no previous month: the change is empty, not zero.

The cases of this rule run through the framework (`statera rules test`), calling the model's pure
function; dbt only runs *unit tests* on SQL models.
