"""Change in records by UF relative to the previous reference month (dbt Python model, with pandas).

  BR-BAR-002  percentage change over the previous month of the same UF; empty in the first reference month

The rule lives in a pure function, `transform(inputs, variables)`, which the framework tests with the
rule's cases (statera rules test). `model()` only wires the function into dbt.
"""

import pandas as pd


def transform(inputs: dict[str, pd.DataFrame], variables: dict) -> pd.DataFrame:
    df = inputs["bar_uf_monthly"][["period", "uf_abbr", "cut_date", "records"]].copy()
    df = df.sort_values(["uf_abbr", "period"]).reset_index(drop=True)
    df["cut_date"] = pd.to_datetime(df["cut_date"]).dt.date  # pandas returns TIMESTAMP; the contract promises DATE
    # BR-BAR-002
    df["previous_month_records"] = df.groupby("uf_abbr")["records"].shift(1)
    df["change_pct"] = ((df["records"] / df["previous_month_records"] - 1) * 100).round(2)
    return df


def model(dbt, session):
    dbt.config(materialized="table", meta={"rules": ["BR-BAR-002"], "gsbpm": "5.7"})
    return transform({"bar_uf_monthly": dbt.ref("bar_uf_monthly").df()}, {})
