"""Metrics defined once (minimal semantic layer).

Each unit's metrics live in `<unit>/5-process/metrics.yaml`, in a format inspired
by the dbt semantic layer (MetricFlow, Apache 2.0 since version 0.209.0):

    simple    aggregation (sum, average, min, max, count) of an expression over the source
    ratio     numerator / denominator (other metrics), times a factor

Each simple metric lists the SOURCES it can be computed from, with the dimensions each one
allows; the framework picks the first source that covers the requested dimensions and filters and
generates the SQL. Filter values go as engine parameters, never concatenated. In production, the same
YAML can be converted into MetricFlow `semantic_models` and `metrics` (see ADR 0013).
"""

from __future__ import annotations

import re

import duckdb
import pandas as pd

from statera import classification, contracts
from statera.project import Project, current

AGGREGATIONS = {"sum": "sum", "average": "avg", "min": "min", "max": "max", "count": "count"}
IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


class MetricError(ValueError):
    """Nonexistent metric, disallowed dimension or invalid filter."""


def definitions(project: Project | None = None) -> dict[str, dict]:
    project = project or current()
    output = {}
    for u in project.units:
        for m in (u.read("5-process/metrics.yaml") or {}).get("metrics", []):
            output[m["name"]] = {**m, "unit": u, "table": ", ".join(f["table"] for f in m.get("sources", []))}
    return output


def _source(m: dict, dims: set[str]) -> dict:
    for source in m["sources"]:
        if dims <= set(source["dimensions"]):
            return source
    raise MetricError(f"{m['name']}: no source allows the dimensions {sorted(dims)}")


def _simple_sql(m: dict, by: list[str], period_range: tuple[str, str] | None, filters: dict) -> tuple[str, list, dict]:
    for name in [*by, *filters]:
        if not IDENTIFIER.match(name):
            raise MetricError(f"invalid dimension: {name!r}")
    source = _source(m, set(by) | set(filters))
    time_col = m["time"]
    groups = ", ".join([time_col, *by])
    conditions, parameters = [], []
    if period_range:
        conditions.append(f"{time_col} between ? and ?")
        parameters += list(period_range)
    for column, value in filters.items():
        conditions.append(f"{column} = ?")
        parameters.append(value)
    where = f" where {' and '.join(conditions)}" if conditions else ""
    sql = (
        f"select {groups}, {AGGREGATIONS[m['aggregation']]}({m['expression']}) as {m['name']} "
        f"from {source['table']}{where} group by {groups} order by {groups}"
    )
    return sql, parameters, source


def compute(
    name: str,
    by: list[str] | None = None,
    period: str | None = None,
    periods: tuple[str, str] | None = None,
    filters: dict | None = None,
    project: Project | None = None,
) -> pd.DataFrame:
    """Computes the metric; the DataFrame carries the provenance in `df.attrs['provenance']`."""
    project = project or current()
    all_metrics = definitions(project)
    if name not in all_metrics:
        raise MetricError(f"metric '{name}' does not exist; available: {sorted(all_metrics)}")
    m, by, filters = all_metrics[name], list(by or []), dict(filters or {})
    period_range = (period, period) if period else periods
    if m["type"] == "ratio":
        num = compute(m["numerator"], by, period, periods, filters, project)
        den = compute(m["denominator"], by, period, periods, filters, project)
        keys = [m["time"], *by]
        df = num.merge(den, on=keys)
        df[name] = (df[m["numerator"]] / df[m["denominator"]].where(df[m["denominator"]] != 0)) * m.get("factor", 1)
        df = df[[*keys, name]]
        df.attrs["provenance"] = {"metric": name, "components": [num.attrs["provenance"], den.attrs["provenance"]]}
        return df
    sql, parameters, source = _simple_sql(m, by, period_range, filters)
    contract = contracts.by_table(project).get(source["table"])
    con = duckdb.connect(str(project.warehouse), read_only=True)
    try:
        df = con.execute(sql, parameters).df()
        cut_column = project.conventions["cut_date_column"]
        cuts = con.execute(f"select distinct cast({cut_column} as varchar) from {source['table']}").fetchall()
    finally:
        con.close()
    values = df[name].dropna()
    if len(values) and values.map(lambda v: float(v).is_integer()).all():
        df[name] = df[name].astype("Int64")  # sums of integers come back as integers
    df.attrs["provenance"] = {
        "metric": name,
        "source": source["table"],
        "contract": contract["id"] if contract else None,
        "classification": classification.summary(classification.of_table(contract)) if contract else None,
        "cut_dates": sorted(c[0] for c in cuts),
        "sql": sql,
    }
    return df
