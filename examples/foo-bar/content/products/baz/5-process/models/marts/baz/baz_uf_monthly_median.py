"""Baz: the legacy R script `baz_uf_monthly_median.R`, run by Rscript inside a dbt Python model.

  BR-BAZ-002  median value by UF and reference month (in the legacy script)

Python only prepares the inputs, calls R and adds what the legacy code did not (UF abbreviation and
cut-off date). The rule stays in the `.R` file, not rewritten. The rule's cases run through the framework
(`statera rules test`) where R is available; the CI installs it and requires them.
"""

from pathlib import Path

import pandas as pd
from statera.legacy import r

SCRIPT = Path(__file__).with_name("baz_uf_monthly_median.R")


def transform(inputs: dict[str, pd.DataFrame], variables: dict) -> pd.DataFrame:
    records = inputs["int_baz__records"][["period", "uf_id", "value"]].astype({"value": float})
    median = r.run(SCRIPT, {"records": records}, output="result")
    median = median.astype({"uf_id": int})
    ufs = inputs["ref_uf"][["uf_id", "uf_abbr"]].astype({"uf_id": int})
    output = median.merge(ufs, on="uf_id", how="left")
    output["cut_date"] = pd.to_datetime(variables["cut_date"]).date()
    output["median_value"] = output["median_value"].round(2)
    return output[["period", "uf_abbr", "cut_date", "median_value"]]


def model(dbt, session):
    dbt.config(materialized="table", meta={"rules": ["BR-BAZ-002"], "gsbpm": "5.7", "legacy": "r"})
    # dbt reads the dbt.ref() calls in the code to build the lineage: the names must be literals
    inputs = {"int_baz__records": dbt.ref("int_baz__records").df(), "ref_uf": dbt.ref("ref_uf").df()}
    return transform(inputs, {"cut_date": dbt.config.get("cut_date")})
