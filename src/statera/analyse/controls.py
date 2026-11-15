"""Declarative controls of the edition (GSBPM 6.2 Validate outputs).

The product declares its controls in `6-analyse/controls.yaml`; the control TYPES belong to the
framework. Identity controls of the current edition live in dbt (singular tests in
`6-analyse/dbt-tests/`); here live those that compare with the previous edition and with history.

Available types:
    revision-between-editions  difference, by key, between the current edition and the previous
                               published one (port, key, measures, relative_base, warning_threshold,
                               error_threshold)
    outlier                    value of the current reference month against the historical mean
                               (port, measure, factor, severity)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import duckdb
import pandas as pd

from statera.analyse.verification import Result


@dataclass
class Inputs:
    period: str
    current: Callable[[str], pd.DataFrame]  # port -> table in the engine
    previous: Callable[[str], pd.DataFrame | None]  # port -> table published in the previous edition
    period_column: str = "period"  # [conventions] period_column in statera.toml


def revisions(current: pd.DataFrame, previous: pd.DataFrame, key: list[str], measures: list[str], base: str):
    """Difference, by key, between the current edition and the previous one."""
    merged = current[key + measures].merge(previous[key + measures], on=key, suffixes=("", "_previous"))
    for col in measures:
        merged[f"revision_{col}"] = merged[col] - merged[f"{col}_previous"]
    reference = merged[f"{base}_previous"].where(merged[f"{base}_previous"] > 0)
    merged["relative_revision"] = (merged[f"revision_{base}"].abs() / reference).fillna(0.0)
    return merged


def _revision(c: dict, ins: Inputs) -> tuple[list[Result], list[dict] | None]:
    previous = ins.previous(c["port"])
    if previous is None or previous.empty:
        return [Result("control", f"{c['id']} {c['name']}", True, "no previous edition to compare", "info")], None
    table = revisions(ins.current(c["port"]), previous, c["key"], c["measures"], c["relative_base"])
    results = []
    for line in table.to_dict("records"):
        rel = line["relative_revision"]
        ok = rel <= c["warning_threshold"]
        severity = "info" if ok else ("error" if rel > c["error_threshold"] else "warning")
        key = "/".join(str(line[k]) for k in c["key"])
        base = c["relative_base"]
        detail = f"{base} {line[f'{base}_previous']} -> {line[base]} ({rel:.1%})"
        results.append(Result("control", f"{c['id']} revision {key}", ok, detail, severity))
    return results, table.to_dict("records")


def _outlier(c: dict, ins: Inputs) -> tuple[list[Result], None]:
    current = ins.current(c["port"])
    history = current.loc[current[ins.period_column] < ins.period, c["measure"]].abs()
    this_period = current.loc[current[ins.period_column] == ins.period, c["measure"]]
    name = f"{c['id']} {c['name']}"
    if history.empty or this_period.empty:
        return [Result("control", name, True, "insufficient history", "info")], None
    mean, value = history.mean(), float(this_period.iloc[0])
    ok = abs(value) <= c["factor"] * max(mean, 1)
    detail = f"{c['measure']}={value:.0f}; previous mean |{c['measure']}|={mean:.0f}; factor {c['factor']}"
    return [Result("control", name, ok, detail, c.get("severity", "warning"))], None


TYPES: dict[str, Callable] = {"revision-between-editions": _revision, "outlier": _outlier}


def run(definitions: list[dict], control_inputs: Inputs) -> tuple[list[Result], dict[str, list[dict]]]:
    """Runs the declared controls. Returns the results and the revision tables per control."""
    results, tables = [], {}
    for definition in definitions:
        try:
            output, table = TYPES[definition["type"]](definition, control_inputs)
        except (duckdb.Error, KeyError) as error:  # e.g. table not built because dbt failed earlier
            name = f"{definition['id']} {definition['name']}"
            output, table = [Result("control", name, False, f"not run: {str(error).splitlines()[0]}")], None
        results += output
        if table is not None:
            tables[definition["id"]] = table
    return results, tables
