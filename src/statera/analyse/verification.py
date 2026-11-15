"""Checking ODCS v3.1 contracts against the data in the engine (GSBPM 5.3 and 6.2).

Covers the subset of the standard used in this repository:
  - schema: existing columns, compatible logical type, physical type, `required`, `unique`,
    primary key (including composite);
  - `library` quality: rowCount, nullValues, missingValues, duplicateValues, invalidValues;
  - `sql` quality: a query returning a number, compared with the mustBe... operators.

In production, the reference tool is `datacontract-cli` (`datacontract test`), which reads the
same files. This checker exists so the demo runs without heavy dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb

from statera import contracts
from statera.project import Project, current

COMPATIBLE_TYPES = {
    "string": ("VARCHAR",),
    "integer": ("INTEGER", "BIGINT", "SMALLINT", "TINYINT", "HUGEINT"),
    "number": ("DOUBLE", "FLOAT", "DECIMAL", "REAL"),
    "date": ("DATE",),
    "timestamp": ("TIMESTAMP",),
    "boolean": ("BOOLEAN",),
}

OPERATORS = {
    "mustBe": lambda v, r: v == r,
    "mustNotBe": lambda v, r: v != r,
    "mustBeGreaterThan": lambda v, r: v > r,
    "mustBeGreaterOrEqualTo": lambda v, r: v >= r,
    "mustBeLessThan": lambda v, r: v < r,
    "mustBeLessOrEqualTo": lambda v, r: v <= r,
    "mustBeBetween": lambda v, r: r[0] <= v <= r[1],
    "mustNotBeBetween": lambda v, r: not (r[0] <= v <= r[1]),
}


@dataclass
class Result:
    contract: str
    verification: str
    ok: bool
    detail: str
    severity: str = "error"

    @property
    def blocking(self) -> bool:
        return not self.ok and self.severity == "error"


def _operator(rule: dict) -> tuple[str, object]:
    for name in OPERATORS:
        if name in rule:
            return name, rule[name]
    raise ValueError(f"Quality rule without an operator: {rule}")


def _metric_library(con, table: str, rule: dict, column: str | None) -> float:
    metric = rule["metric"]
    if metric == "rowCount":
        return con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    if column is None:
        raise ValueError(f"Metric {metric} requires a column")
    if metric == "nullValues":
        return con.execute(f"SELECT count(*) FROM {table} WHERE {column} IS NULL").fetchone()[0]
    if metric == "missingValues":
        sql = f"SELECT count(*) FROM {table} WHERE {column} IS NULL OR CAST({column} AS VARCHAR) = ''"
        return con.execute(sql).fetchone()[0]
    if metric == "duplicateValues":
        sql = f"SELECT count({column}) - count(DISTINCT {column}) FROM {table}"
        return con.execute(sql).fetchone()[0]
    if metric == "invalidValues":
        valid_values = rule.get("arguments", {}).get("validValues", [])
        entries = ", ".join("'" + str(v).replace("'", "''") + "'" for v in valid_values)
        sql = (
            f"SELECT count(*) FROM {table} WHERE {column} IS NOT NULL AND CAST({column} AS VARCHAR) NOT IN ({entries})"
        )
        return con.execute(sql).fetchone()[0]
    raise ValueError(f"Unsupported metric: {metric}")


def _cut_filter(contract: dict, cut: str | None) -> str | None:
    """Tables that hold several cuts are checked at the run's cut (statera profile)."""
    if not cut:
        return None
    if partition := contracts.custom_property_of(contract, "partitionedBy"):
        return f"{partition} = CAST('{cut}' AS DATE)"
    if extraction := contracts.custom_property_of(contract, "extraction"):
        return f"CAST({extraction['receivedAtColumn']} AS DATE) <= CAST('{cut}' AS DATE)"
    return None


def _evaluate_quality(
    con, cid: str, table: str, rule: dict, column: str | None, physical_name: str | None = None
) -> Result:
    name = rule.get("id") or rule.get("name") or rule.get("metric") or "quality"
    target = f"{column}.{name}" if column else name
    severity = rule.get("severity", "error")
    try:
        kind = rule.get("type", "library")
        if kind == "sql":
            query = (
                rule["query"].replace(physical_name, table)
                if physical_name and physical_name != table
                else rule["query"]
            )
            value = con.execute(query).fetchone()[0]
        elif kind == "library" or "metric" in rule:
            value = _metric_library(con, table, rule, column)
        else:
            return Result(cid, target, True, f"type '{kind}' cannot run here", "info")
        operator, reference = _operator(rule)
        ok = OPERATORS[operator](float(value), reference)
        detail = f"value={value} {operator} {reference}"
        if rule.get("description"):
            detail += f" — {rule['description'].strip()}"
        return Result(cid, target, ok, detail, severity)
    except duckdb.Error as error:
        return Result(cid, target, False, f"execution error: {error}", severity)


def _check_property(con, cid: str, table: str, prop: dict, actual_columns: dict) -> list[Result]:
    col = prop["name"]
    if col not in actual_columns:
        return [Result(cid, f"{col}.exists", False, "missing column")]
    results = []
    actual_type = actual_columns[col]
    logical = prop.get("logicalType")
    if logical and not actual_type.startswith(COMPATIBLE_TYPES.get(logical, (actual_type,))):
        results.append(Result(cid, f"{col}.type", False, f"logical {logical} incompatible with {actual_type}"))
    physical_type = (prop.get("physicalType") or "").upper().replace(" ", "")
    if physical_type and physical_type != actual_type.replace(" ", ""):
        results.append(Result(cid, f"{col}.physical_type", False, f"expected {physical_type}, found {actual_type}"))
    if prop.get("required"):
        nulls = con.execute(f"SELECT count(*) FROM {table} WHERE {col} IS NULL").fetchone()[0]
        results.append(Result(cid, f"{col}.required", nulls == 0, f"nulls={nulls}"))
    if prop.get("unique"):
        dup = con.execute(f"SELECT count({col}) - count(DISTINCT {col}) FROM {table}").fetchone()[0]
        results.append(Result(cid, f"{col}.unique", dup == 0, f"duplicates={dup}"))
    results += [_evaluate_quality(con, cid, table, rule, col) for rule in prop.get("quality", []) or []]
    return results


def check(contract: dict, con: duckdb.DuckDBPyConnection, cut: str | None = None) -> list[Result]:
    cid = contract["id"]
    results: list[Result] = []
    filter_ = _cut_filter(contract, cut)
    for obj in contract.get("schema", []):
        physical_name = obj.get("physicalName", obj["name"])
        schema, name = physical_name.split(".")
        table = f"(SELECT * FROM {physical_name} WHERE {filter_})" if filter_ else physical_name
        actual_columns = {
            line[0]: line[1].upper()
            for line in con.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = ? AND table_name = ?",
                [schema, name],
            ).fetchall()
        }
        if not actual_columns:
            results.append(Result(cid, physical_name, False, "table does not exist in the warehouse"))
            continue

        keys = []
        for prop in obj.get("properties", []):
            results += _check_property(con, cid, table, prop, actual_columns)
            if prop.get("primaryKey") and prop["name"] in actual_columns:
                keys.append((prop.get("primaryKeyPosition", 1), prop["name"]))

        if len(keys) > 1:
            cols = ", ".join(c for _, c in sorted(keys))
            dup = con.execute(
                f"SELECT count(*) FROM (SELECT {cols} FROM {table} GROUP BY {cols} HAVING count(*) > 1)"
            ).fetchone()[0]
            results.append(Result(cid, f"key({cols})", dup == 0, f"duplicate keys={dup}"))

        for rule in obj.get("quality", []) or []:
            results.append(_evaluate_quality(con, cid, table, rule, None, physical_name))
    return results


def check_all(ids: set[str] | None = None, project: Project | None = None, cut: str | None = None) -> list[Result]:
    """Checks the contracts (all, or only those in `ids`) against the project engine, at the cut if given."""
    project = project or current()
    con = duckdb.connect(str(project.warehouse), read_only=True)
    results = []
    try:
        for cid, (_, contract) in sorted(contracts.all_(project).items()):
            if ids is not None and cid not in ids:
                continue
            if contracts.custom_property_of(contract, "manualProduction"):
                continue  # produced outside the process (level 1): the table is not in the engine
            results.extend(check(contract, con, cut))
    finally:
        con.close()
    return results
