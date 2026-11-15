"""Baz: the legacy SPSS script `baz_uf_monthly.sps`, run by GNU PSPP inside a dbt Python model.

  BR-BAZ-001  mean value by UF and reference month (in the legacy script)

Python only prepares the inputs, calls PSPP and adds what the legacy code did not (UF abbreviation and
cut-off date). The rule stays in the `.sps` file, not rewritten. The rule's cases run through the framework
(`statera rules test`) where PSPP is available; the CI installs it and requires them.
"""

from pathlib import Path

import pandas as pd
from statera.legacy import pspp

SYNTAX = Path(__file__).with_name("baz_uf_monthly.sps")


def transform(inputs: dict[str, pd.DataFrame], variables: dict) -> pd.DataFrame:
    records = inputs["int_baz__records"][["period", "uf_id", "value"]].astype({"value": float})
    aggregated = pspp.run(SYNTAX, {"records": records}, output="result")
    aggregated = aggregated.astype({"uf_id": int, "records": int})
    ufs = inputs["ref_uf"][["uf_id", "uf_abbr"]].astype({"uf_id": int})
    output = aggregated.merge(ufs, on="uf_id", how="left")
    output["cut_date"] = pd.to_datetime(variables["cut_date"]).date()
    output["mean_value"] = output["mean_value"].round(2)
    return output[["period", "uf_abbr", "cut_date", "records", "mean_value"]]


def model(dbt, session):
    dbt.config(materialized="table", meta={"rules": ["BR-BAZ-001"], "gsbpm": "5.7", "legacy": "spss"})
    # dbt reads the dbt.ref() calls in the code to build the lineage: the names must be literals
    inputs = {"int_baz__records": dbt.ref("int_baz__records").df(), "ref_uf": dbt.ref("ref_uf").df()}
    return transform(inputs, {"cut_date": dbt.config.get("cut_date")})
