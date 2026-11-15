"""Evaluation of each edition (GSBPM 8.1 Gather evaluation inputs, 8.2 Conduct evaluation).

Indicators are DECLARED in `<product>/8-evaluate/indicators.yaml`; none requires product code.
Each one states how it is computed:

    calculation: {function: <name>, ...parameters}   generic function of this framework (`FUNCTIONS`)
    calculation: {sql: <query>}                      SQL in the engine, with {{ period }}, {{ cut_date }}
                                                     and {{ published('<port>') }} (the edition's published CSV)

The result goes to `<product>/8-evaluate/data/<period>/evaluation.{json,md}` and, when the
edition is published, to the editions register, next to the Data Package. The action plan (8.3)
lives in indicators.yaml: it changes when the team decides on an action, not at every edition.
"""

from __future__ import annotations

import calendar
import json
import operator
from collections.abc import Callable
from datetime import date
from pathlib import Path

import duckdb
import jinja2
import pandas as pd

from statera.design import bpmn
from statera.disseminate import registry
from statera.edition import Edition, read_jsonl
from statera.project import PACKAGE, Unit

OPERATORS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le}
OPEN_FORMATS = {"csv", "json", "md"}


def indicators(unit: Unit) -> dict:
    return unit.read("8-evaluate/indicators.yaml") or {"indicators": []}


def _period_end(period: str) -> date:
    year, month = map(int, period.split("-"))
    return date(year, month, calendar.monthrange(year, month)[1])


def _process(unit: Unit) -> bpmn.Process | None:
    path = unit.to_be_process.with_suffix(".bpmn")
    return bpmn.read(path) if path.exists() else None


def _datapackage(ed: Edition) -> dict:
    path = ed.data_folder(7) / "datapackage.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"resources": []}


# ---------------------------------------------------------------------------
# Generic functions (calculation: {function: ...})
# ---------------------------------------------------------------------------


def dissemination_lag(ed: Edition, _: dict) -> float | None:
    release_calendar = {e["period"]: e for e in ed.unit.read("1-specify/calendar.yaml")["editions"]}
    planned = release_calendar.get(ed.period, {}).get("release")
    return (date.fromisoformat(planned) - _period_end(ed.period)).days if planned else None


def cut_date_lag(ed: Edition, _: dict) -> float:
    return (date.fromisoformat(ed.cut_date) - _period_end(ed.period)).days


def max_relative_revision(ed: Edition, params: dict) -> float:
    lines = (ed.revisions or {}).get(params["control"]) or []
    return round(100 * max((r["relative_revision"] for r in lines), default=0.0), 2)


def check_failures(ed: Edition, _: dict) -> float:
    return ed.non_blocking_failures + ed.dbt_results.get("failed_tests", 0)


def checks_run(ed: Edition, _: dict) -> float:
    return ed.checks + ed.dbt_results.get("tests", 0)


def open_format_files(ed: Edition, _: dict) -> float | None:
    formats = [r["format"] for r in _datapackage(ed)["resources"]] + ["json", "json"]  # datapackage and ckan
    return round(100 * sum(f in OPEN_FORMATS for f in formats) / len(formats), 1)


def relative_automated_duration(ed: Edition, _: dict) -> float | None:
    process = _process(ed.unit)
    lines = [
        r
        for r in read_jsonl(ed.unit.runs_folder / "runs.jsonl")
        if r["period"] == ed.period
        and r["cutDate"] == ed.cut_date
        and r["status"] == "ok"
        and r.get("duration_s") is not None
    ]
    if process is None or not lines:
        return None
    observed = pd.DataFrame(lines).groupby("bpmn_task")["duration_s"].last().sum()
    modeled = bpmn.summary(process)["automatic_h"] * 3600
    return round(100 * observed / modeled, 1) if modeled else None


def modeled_manual_hours(ed: Edition, _: dict) -> float | None:
    process = _process(ed.unit)
    return round(bpmn.summary(process)["manual_h"], 2) if process else None


FUNCTIONS: dict[str, Callable[[Edition, dict], float | None]] = {
    f.__name__: f
    for f in (
        dissemination_lag,
        cut_date_lag,
        max_relative_revision,
        check_failures,
        checks_run,
        open_format_files,
        relative_automated_duration,
        modeled_manual_hours,
    )
}


# ---------------------------------------------------------------------------
# SQL indicators (calculation: {sql: ...})
# ---------------------------------------------------------------------------


def render_sql(sql: str, period: str, cut_date: str, published: Callable[[str], str]) -> str:
    # SQL from the repository itself (reviewed via PR), not HTML: escaping would corrupt the query.
    return (
        jinja2.Environment(undefined=jinja2.StrictUndefined, autoescape=False)
        .from_string(sql)
        .render(period=period, cut_date=cut_date, published=published)
    )


def _sql(ed: Edition, sql: str) -> float | None:
    def published(port: str) -> str:
        name = registry.series_config(ed.unit)["files_per_edition"]["tables"].format(port=port)
        return f"read_csv_auto('{ed.data_folder(7) / name}')"

    con = duckdb.connect(str(ed.project.warehouse), read_only=True)
    try:
        line = con.execute(render_sql(sql, ed.period, ed.cut_date, published)).fetchone()
    finally:
        con.close()
    return None if not line or line[0] is None else float(line[0])


def compute_one(ed: Edition, definition: dict) -> float | None:
    calculation = definition["calculation"]
    if "sql" in calculation:
        return _sql(ed, calculation["sql"])
    return FUNCTIONS[calculation["function"]](ed, calculation)


def declaration_problems(unit: Unit) -> list[str]:
    """Indicators without a valid calculation (checked in CI, no data needed)."""
    errors = []
    for d in indicators(unit)["indicators"]:
        calculation = d.get("calculation") or {}
        if "function" in calculation and calculation["function"] not in FUNCTIONS:
            errors.append(
                f"{d['id']}: function '{calculation['function']}' does not exist in the framework ({sorted(FUNCTIONS)})"
            )
        elif "sql" in calculation:
            try:
                render_sql(calculation["sql"], "2000-01", "2000-02-20", lambda port: f"table_{port}")
            except jinja2.TemplateError as error:
                errors.append(f"{d['id']}: SQL does not render: {error}")
        elif not calculation:
            errors.append(f"{d['id']}: no `calculation`")
    return errors


# ---------------------------------------------------------------------------
# Evaluation (GSBPM 8.2) and report
# ---------------------------------------------------------------------------


def _format_value(value, unit: str) -> str:
    if value is None:
        return "n/a"
    number = f"{value:,.2f}".rstrip("0").rstrip(".")
    return f"{number} {unit}".strip()


def compute(ed: Edition) -> list[dict]:
    output = []
    for definition in indicators(ed.unit)["indicators"]:
        value = compute_one(ed, definition)
        alert = definition.get("alert")
        in_alert = bool(alert and value is not None and OPERATORS[alert["operator"]](value, alert["value"]))
        output.append(
            {
                **{k: v for k, v in definition.items() if k != "calculation"},
                "value": value,
                "formatted_value": _format_value(value, definition["unit"]),
                "alert_text": f"{alert['operator']} {alert['value']}" if alert else "—",
                "in_alert": in_alert,
            }
        )
    return output


def evaluate(ed: Edition) -> Path:
    """Computes the indicators and writes JSON and the report to <product>/8-evaluate/data/<period>/."""
    results = compute(ed)
    context = {
        "product": ed.product,
        "period": ed.period,
        "cut_date": ed.cut_date,
        "version": ed.publication.get("version", ""),
        "run": ed.run_id,
        "indicators": results,
        "alerts": [r for r in results if r["in_alert"]],
        "actions": [a for a in indicators(ed.unit).get("action_plan", []) if a["status"] != "done"],
        "synthetic": ed.project.synthetic,
    }
    folder = ed.data_folder(8)
    folder.mkdir(parents=True, exist_ok=True)
    data = {k: v for k, v in context.items() if k != "actions"}
    (folder / "evaluation.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    environment = jinja2.Environment(
        loader=jinja2.FileSystemLoader(PACKAGE / "evaluate"),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        autoescape=jinja2.select_autoescape(["html"], default=False),
    )
    report = folder / "evaluation.md"
    report.write_text(environment.get_template("evaluation-template.md.j2").render(**context), encoding="utf-8")
    return report


def history(unit: Unit) -> pd.DataFrame:
    """One row per PUBLISHED and evaluated edition, one column per indicator: the basis for trends."""
    lines = []
    for file in sorted(registry.evaluations_folder(unit).glob("*.json")):
        data = json.loads(file.read_text(encoding="utf-8"))
        line = {"period": data["period"]}
        line.update({i["id"]: i["formatted_value"] + (" ⚠" if i["in_alert"] else "") for i in data["indicators"]})
        lines.append(line)
    return pd.DataFrame(lines)
