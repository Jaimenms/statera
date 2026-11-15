"""Catalog of recurring queries (call for proposals, item 13), parameterized and tested.

A query is a folder in `queries/<theme>/<slug>/` (cross-cutting) or in
(statera 0.7: there are no queries inside products; the `product` axis says which one a query serves), with:

    query.yaml       metadata: the five axes, parameters, rules, limitations,
                        output classification, owner, status, version
    <slug_with_underscores>.sql
                        a dbt *analysis*: uses ref() (gains lineage and documentation) and
                        var('<parameter>', <default>) for the parameters; does not become a table
    test.yaml          cases with synthetic input data and expected output

Run: parameters are validated by the declared TYPE (never concatenated without validation), `ref()` is
resolved by the same rules dbt follows (reading needs no dbt), and the output goes through the statistical
disclosure control declared in `output_classification` before being written with a provenance Data Package.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import duckdb
import jinja2
import numpy as np
import pandas as pd

from statera.consume import connection
from statera.edition import now
from statera.process import dbt
from statera.project import Project, current, read_yaml

VALIDATORS = {
    "period": re.compile(r"^\d{4}-(0[1-9]|1[0-2])$"),
    "ibge-code": re.compile(r"^\d{7}$"),
    "uf": re.compile(r"^[A-Z]{2}$"),
    "integer": re.compile(r"^-?\d+$"),
    "short-text": re.compile(r"^[\w\s.,-]{1,60}$"),
    "cut": re.compile(r"^(latest|\d{4}-\d{2}-\d{2})$"),  # cut-off date, or "latest" (the last released)
}


FORMATS = {
    "period": "YYYY-MM (reference period, e.g. 2026-06)",
    "date": "YYYY-MM-DD",
    "cut": "YYYY-MM-DD cut-off date, or latest (the last released cut)",
    "ibge-code": "7-digit IBGE code (e.g. 3550308)",
    "uf": "2-letter UF code, uppercase (e.g. SP)",
    "integer": "integer",
    "short-text": "short text (letters, digits, spaces and . , -; up to 60 characters)",
}


class QueryError(ValueError):
    """Invalid parameter, nonexistent query or result differing from the expected one."""


@dataclass
class Query:
    folder: Path
    meta: dict

    @property
    def id(self) -> str:
        return self.meta["id"]

    @property
    def title(self) -> str:
        return self.meta["title"]

    @property
    def dbt_name(self) -> str:
        return self.folder.name.replace("-", "_")

    @property
    def sql(self) -> Path:
        return self.folder / f"{self.dbt_name}.sql"

    @property
    def test_file(self) -> Path:
        return self.folder / "test.yaml"


def load(project: Project | None = None) -> list[Query]:
    project = project or current()
    return sorted(
        (
            Query(c.parent, read_yaml(c))
            for base in project.query_folders()
            if base.is_dir()
            for c in base.rglob("query.yaml")
        ),
        key=lambda c: c.id,
    )


def get(identifier: str, project: Project | None = None) -> Query:
    for c in load(project):
        if identifier in (c.id, c.id.rsplit(".", 1)[-1], c.folder.name):
            return c
    raise QueryError(f"Query '{identifier}' not found")


def validate_parameters(query: Query, values: dict) -> dict:
    """Final parameter values, validated by the declared type."""
    declared_params = {p["name"]: p for p in query.meta.get("parameters", [])}
    if extras := set(values) - set(declared_params):
        raise QueryError(f"Undeclared parameters: {sorted(extras)} (statera query show {query.id} --help)")
    final = {}
    for name, p in declared_params.items():
        value = values.get(name, p.get("default"))
        if value is None:
            if p.get("required"):
                raise QueryError(f"Missing required parameter: {name} (statera query show {query.id} --help)")
            continue
        value = str(value)
        if p["type"] == "date":
            date.fromisoformat(value)
        elif not VALIDATORS[p["type"]].match(value):
            raise QueryError(f"Parameter {name}={value!r} is not of type {p['type']}: {FORMATS[p['type']]}")
        final[name] = value
    return final


def render(query: Query, parameters: dict, relations: dict[str, str]) -> str:
    """Final SQL: ref() -> relation in the engine; var() -> already validated parameter."""

    def ref(name: str) -> str:
        if name not in relations:
            raise QueryError(f"{query.id}: ref('{name}') has no known relation")
        return relations[name]

    def var(name: str, default=None):
        return parameters.get(name, default)

    # SQL from the repository (reviewed via PR) with type-validated parameters; it is not HTML.
    environment = jinja2.Environment(undefined=jinja2.StrictUndefined, autoescape=False)
    return environment.from_string(query.sql.read_text(encoding="utf-8")).render(ref=ref, var=var)


def apply_control(df: pd.DataFrame, control: dict | None) -> tuple[pd.DataFrame, int]:
    """Statistical disclosure control on the query output (same rule as publication)."""
    from statera.analyse.disclosure_control import apply

    result = apply(df, control)
    return result.data, result.suppressed_cells


def describe(identifier: str, project: Project | None = None) -> str:
    """What someone needs to run a query: what it answers, its parameters (type, format, required or default),
    limitations, output classification and a command to copy."""
    query = get(identifier, project)
    meta = query.meta
    lines = [f"{query.id} — {query.title}", ""]
    if description := (meta.get("description") or "").strip():
        lines += [description, ""]
    parameters = meta.get("parameters", [])
    lines.append("Parameters (--param name=value):" if parameters else "Parameters: none")
    width = max((len(p["name"]) for p in parameters), default=0)
    for p in parameters:
        need = "required" if p.get("required") else f"default {p['default']}" if "default" in p else "optional"
        text = f" — {p['description']}" if p.get("description") else ""
        lines.append(f"  {p['name']:<{width}}  {p['type']}: {FORMATS.get(p['type'], p['type'])}; {need}{text}")
    example = " ".join(
        f"--param {p['name']}={p.get('default') or _EXAMPLES.get(p['type'], '...')}"
        for p in parameters
        if p.get("required") or "default" in p
    )
    if limitations := (meta.get("limitations") or "").strip():
        lines += ["", f"Limitations: {limitations}"]
    out = meta.get("output_classification") or {}
    if out:
        shown = " · ".join(f"{k} {v}" for k, v in out.items() if k != "disclosureControl")
        control = " · with disclosure control" if out.get("disclosureControl") else ""
        lines.append(f"Output: {shown}{control}")
    lines.append(
        f"Owner: {meta.get('owner', '—')} · product: {meta.get('product', '—')} · status: {meta.get('status', '—')} "
        f"· version: {meta.get('version', '—')}"
    )
    command = " ".join(part for part in (query.id, example) if part)
    lines += [
        "",
        "Examples:",
        f"  statera query show {command}     # the plain SQL",
        f"  statera query run {command} --output result/",
    ]
    return "\n".join(lines) + "\n"


_EXAMPLES = {"period": "2026-06", "date": "2026-06-30", "cut": "latest", "ibge-code": "3550308", "uf": "SP",
             "integer": "1", "short-text": "text"}  # fmt: skip


def compiled_sql(identifier: str, values: dict | None = None, project: Project | None = None) -> str:
    """The SQL that `run` executes, with ref() resolved to the engine's tables and var() to the parameters
    (validated by type; defaults where not given): plain SQL to read, copy or run in another tool."""
    project = project or current()
    query = get(identifier, project)
    parameters = validate_parameters(query, values or {})
    return _compiled(query, parameters, project)


def _compiled(query: Query, parameters: dict, project: Project) -> str:
    sql = render(query, parameters, dbt.relations(project)).strip()
    shown = ", ".join(f"{k}={v}" for k, v in parameters.items()) or "none"
    return f"-- {query.id} {query.meta.get('version', '')} · parameters: {shown}\n{sql}\n"


def run(
    identifier: str,
    values: dict | None = None,
    output: Path | None = None,
    project: Project | None = None,
    engine: str | None = None,
):
    """Runs on the engine (`engine`: see statera.consume.connection). With `output`, writes result.csv and a
    provenance datapackage.json."""
    project = project or current()
    query = get(identifier, project)
    parameters = validate_parameters(query, values or {})
    sql = _compiled(query, parameters, project)
    df = connection.fetch(sql, project=project, engine=engine)
    df, suppressed = apply_control(df, query.meta["output_classification"].get("disclosureControl"))
    provenance = {
        "query": query.id,
        "version": query.meta["version"],
        "parameters": parameters,
        "executed_at": now(),
        "engine": connection.describe(project, engine),
        "suppressed_cells": suppressed,
        "sql_sha256": hashlib.sha256(sql.encode()).hexdigest(),
    }
    df.attrs["provenance"] = provenance
    if output:
        _save(df, query, provenance, output)
        (Path(output) / "query.sql").write_text(sql, encoding="utf-8")  # exactly what ran
    return df


def _save(df: pd.DataFrame, query: Query, provenance: dict, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    file = output / "result.csv"
    df.to_csv(file, index=False, lineterminator="\n")
    package = {
        "$schema": "https://datapackage.org/profiles/2.0/datapackage.json",
        "name": query.id.rsplit(".", 1)[-1],
        "title": query.title,
        "resources": [
            {
                "name": "result",
                "path": file.name,
                "format": "csv",
                "hash": f"sha256:{hashlib.sha256(file.read_bytes()).hexdigest()}",
            }
        ],
        "x-statera": provenance,
    }
    if authors := query.meta.get("authors"):
        package["contributors"] = [
            {"title": a["name"], "roles": ["author"], **({"organization": a["unit"]} if a.get("unit") else {})}
            for a in authors
        ]
    (output / "datapackage.json").write_text(json.dumps(package, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests (synthetic data, without the engine): same principle as dbt unit tests
# ---------------------------------------------------------------------------


def _value(v) -> str:
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return "None"
    if isinstance(v, bool | np.bool_):
        return str(bool(v))
    if isinstance(v, int | float) or hasattr(v, "item"):
        number = float(v)
        return str(int(number)) if number.is_integer() else f"{number:.6g}"
    return str(v)


def _normalize(df: pd.DataFrame, columns: list[str]) -> list[tuple]:
    lines = df[columns].astype(object).values.tolist()
    return sorted(tuple(_value(v) for v in line) for line in lines)


def test(query: Query) -> list[str]:
    """Runs the test.yaml cases in an in-memory DuckDB. Returns the failures (empty = ok)."""
    if not query.test_file.exists():
        return [f"{query.id}: no test.yaml"]
    failures = []
    for case in read_yaml(query.test_file)["cases"]:
        con = duckdb.connect()
        try:
            relations = {}
            for name, lines in case["inputs"].items():
                con.register(f"_df_{name}", pd.DataFrame(lines))
                con.execute(f"CREATE TABLE t_{name} AS SELECT * FROM _df_{name}")
                relations[name] = f"t_{name}"
            sql = render(query, validate_parameters(query, case["parameters"]), relations)
            actual, _ = apply_control(
                con.execute(sql).df(), query.meta["output_classification"].get("disclosureControl")
            )
        except (duckdb.Error, QueryError, KeyError) as error:
            failures.append(f"{query.id} [{case['id']}]: error while running: {error}")
            continue
        finally:
            con.close()
        expected = pd.DataFrame(case["expected"])
        columns = list(expected.columns)
        if missing := [c for c in columns if c not in actual.columns]:
            failures.append(f"{query.id} [{case['id']}]: columns missing from the result: {missing}")
        elif _normalize(actual, columns) != _normalize(expected, columns):
            failures.append(
                f"{query.id} [{case['id']}]: result differs from the expected\n{actual[columns].to_string(index=False)}"
            )
    return failures
