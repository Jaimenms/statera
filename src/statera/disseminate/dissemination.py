"""Generation of an edition package (GSBPM 7.1 and 7.2), with nothing product-specific.

Everything is metadata-driven:
  - what to publish: the product's output ports (ODPS);
  - where to read from, description, limitations, schema and statistical disclosure control: ODCS contracts;
  - what may be published: approved classification (phase 6 gate);
  - file names, license, main port: the series (7-disseminate/series.yaml);
  - the summary text: the series' Jinja2 template, filled with the published tables.

Outputs in `<product>/7-disseminate/data/<period>/`: one CSV per port, xlsx spreadsheet, summary,
datapackage.json (Frictionless Data Package 2.0) and ckan.json (payload for a CKAN open data portal).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import duckdb
import jinja2
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from statera import classification, contracts, governance, ports
from statera.analyse.disclosure_control import Table
from statera.project import Unit

MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]
FRICTIONLESS_TYPE = {
    "string": "string",
    "integer": "integer",
    "number": "number",
    "date": "date",
    "timestamp": "datetime",
    "boolean": "boolean",
}
MEDIATYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "md": "text/markdown",
}


# ---------------------------------------------------------------------------
# Number formatting (summary filters)
# ---------------------------------------------------------------------------


def thousands(v) -> str:
    """1234567 -> '1,234,567'."""
    return f"{int(v):,}"


def signed(v) -> str:
    """Integer with an explicit sign when positive: 1234 -> '+1,234'."""
    return ("+" if int(v) > 0 else "") + thousands(v)


def pct(v) -> str:
    """Signed percentage with two decimals: 1.5 -> '+1.50%'."""
    return f"{float(v):+.2f}%"


def brl(v) -> str:
    """Brazilian real amount: 1234.5 -> 'R$ 1,234.50'."""
    return f"R$ {float(v):,.2f}"


def period_long(period: str) -> str:
    """'2026-02' -> 'February 2026'."""
    year, month = period.split("-")
    return f"{MONTHS[int(month) - 1]} {year}"


# ---------------------------------------------------------------------------
# Loading the output ports
# ---------------------------------------------------------------------------


def load_tables(unit: Unit) -> list[Table]:
    all_ = contracts.all_(unit.project)
    con = duckdb.connect(str(unit.project.warehouse), read_only=True)
    tables = []
    try:
        for port in ports.output_ports(unit):
            _, contract = all_[port["contractId"]]
            tab = contracts.custom_property_of(port, "spreadsheetTab", port["name"])[:31]
            description = port.get("description", port["name"])
            t = Table(port["name"], description, tab, port["contractId"], contract, ports.read(contract, con))
            t.in_spreadsheet = contracts.custom_property_of(port, "inSpreadsheet", True) is not False
            tables.append(t)
    finally:
        con.close()
    return tables


# ---------------------------------------------------------------------------
# Spreadsheet
# ---------------------------------------------------------------------------

HEADER_STYLE = Font(bold=True, color="FFFFFF")
HEADER_BACKGROUND = PatternFill("solid", fgColor="1F4E79")


def _cell_format(column: str, prop: dict | None) -> str | None:
    if column.endswith("_pct"):
        return "0.00"
    kind = (prop or {}).get("logicalType")
    if kind == "date":
        return "yyyy-mm-dd"
    if kind == "number":
        return "#,##0.00"
    if kind == "integer" and not column.endswith(("_id", "_code")):  # identifiers are not quantities
        return "#,##0"
    return None


def _notes(tables: list[Table], context: dict) -> list[tuple]:
    lines = [(f"{context['product']['name']} — {context['edition']['period_long']}", None)]
    if context["synthetic"]:
        lines.append(("SYNTHETIC DATA — DEMONSTRATION. Do not use for analysis.", None))
    ed = context["edition"]
    lines += [
        ("Reference period", ed["period"]),
        ("Extraction cut-off date", ed["cut_date"]),
        ("Record version", ed["version"]),
        ("Generated at", ed["generated_at"]),
        ("Code version", ed["code_version"]),
        ("", None),
        ("Tables", None),
    ]
    for t in tables:
        lines.append((t.description, t.contract["description"].get("purpose", "").strip()))
        if limitations := t.contract["description"].get("limitations"):
            lines.append(("    Limitations", " ".join(limitations.split())))
    for t in tables:
        if t.applied_control:
            c = t.control
            text = f"Suppressed by disclosure control: {c['column']} < {c['minimum']} ({t.description})."
            lines += [("", None), ("Cells marked with X", text)]
    return lines


def generate_xlsx(path: Path, tables: list[Table], context: dict) -> None:
    wb = Workbook()
    notes = wb.active
    notes.title = "Notes"
    for i, (a, b) in enumerate(_notes(tables, context), start=1):
        notes.cell(row=i, column=1, value=a)
        if b is not None:
            notes.cell(row=i, column=2, value=b).alignment = Alignment(wrap_text=True)
    notes["A1"].font = Font(bold=True, size=14)
    if context["synthetic"]:
        notes["A2"].font = Font(bold=True, color="C00000")
    notes.column_dimensions["A"].width = 38
    notes.column_dimensions["B"].width = 110
    for t in tables:
        ws = wb.create_sheet(t.sheet)
        props = {p["name"]: p for p in t.obj["properties"]}
        df = t.data
        for j, col in enumerate(df.columns, start=1):
            c = ws.cell(row=1, column=j, value=col)
            c.font, c.fill = HEADER_STYLE, HEADER_BACKGROUND
            ws.column_dimensions[get_column_letter(j)].width = max(12, len(col) + 2)
        for i, line in enumerate(df.itertuples(index=False), start=2):
            for j, (col, value) in enumerate(zip(df.columns, line, strict=True), start=1):
                if pd.isna(value):
                    value = "X" if col in t.suppressed_columns else None
                elif hasattr(value, "item"):
                    value = value.item()
                cell = ws.cell(row=i, column=j, value=value)
                fmt = _cell_format(col, props.get(col))
                if fmt and not isinstance(value, str):
                    cell.number_format = fmt
        ws.freeze_panes = "A2"
    wb.save(path)


# ---------------------------------------------------------------------------
# Summary (the series' Jinja2 template)
# ---------------------------------------------------------------------------


def _environment(folder: Path, period: str, period_column: str = "period") -> jinja2.Environment:
    environment = jinja2.Environment(
        loader=jinja2.FileSystemLoader(folder),
        undefined=jinja2.StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        autoescape=jinja2.select_autoescape(["html"], default=False),  # Markdown output
    )
    environment.filters.update(
        thousands=thousands,
        signed=signed,
        pct=pct,
        brl=brl,
        in_period=lambda rows: [r for r in rows if str(r.get(period_column)) == period],
    )
    environment.globals.update(
        top=lambda rows, col, n: sorted(rows, key=lambda r: r[col], reverse=True)[:n],
        bottom=lambda rows, col, n: sorted(rows, key=lambda r: r[col])[:n],
        zip=zip,
    )
    return environment


def _records(df: pd.DataFrame) -> list[dict]:
    return json.loads(df.to_json(orient="records", date_format="iso", force_ascii=False))


def generate_summary(unit: Unit, model: str, tables: list[Table], context: dict) -> str:
    controls = {
        t.port: {
            **(t.control or {}),
            "suppressed_cells": t.suppressed_cells,
            "removed_rows": t.removed_rows,
        }
        for t in tables
    }
    environment = _environment(unit.phase(7), context["edition"]["period"], unit.project.conventions["period_column"])
    return environment.get_template(model).render(
        tables={t.port: _records(t.data) for t in tables}, controls=controls, **context
    )


# ---------------------------------------------------------------------------
# Metadata: Data Package and CKAN
# ---------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def code_version(root: Path) -> str:
    try:
        output = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True, text=True, check=True
        )
        return output.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "no-git"


def _schema_frictionless(t: Table) -> dict:
    fields = []
    for prop in t.obj["properties"]:
        if prop["name"] not in t.data.columns:
            continue
        field = {"name": prop["name"], "type": FRICTIONLESS_TYPE[prop["logicalType"]]}
        if prop.get("description"):
            field["description"] = prop["description"]
        fields.append(field)
    if "suppressed" in t.data.columns:
        fields.append(
            {"name": "suppressed", "type": "boolean", "description": "Cell suppressed by disclosure control."}
        )
    return {"fields": fields, "missingValues": [""]}


def generate_datapackage(
    folder: Path, tables: list[Table], extras: list[Path], context: dict, series_config: dict
) -> dict:
    product, ed = context["product"], context["edition"]
    resources = []
    for t in tables:
        file = folder / series_config["files_per_edition"]["tables"].format(port=t.port)
        resources.append(
            {
                "name": t.port,
                "title": t.description,
                "description": t.contract["description"].get("purpose", "").strip(),
                "path": file.name,
                "format": "csv",
                "mediatype": "text/csv",
                "encoding": "utf-8",
                "bytes": file.stat().st_size,
                "hash": f"sha256:{_sha256(file)}",
                "schema": _schema_frictionless(t),
                "x-statera": {
                    "contract": t.contract_id,
                    "urn": contracts.custom_property_of(t.contract, "urn"),
                    "contractVersion": t.contract["version"],
                    "classification": classification.summary(classification.of_table(t.contract)),
                    "suppressedCells": t.suppressed_cells,
                    **({"removedRows": t.removed_rows} if t.removed_rows else {}),
                },
            }
        )
    for file in extras:
        format_ = file.suffix.lstrip(".")
        resources.append(
            {
                "name": file.stem.lower().replace("_", "-"),
                "path": file.name,
                "format": format_,
                "mediatype": MEDIATYPES[format_],
                "bytes": file.stat().st_size,
                "hash": f"sha256:{_sha256(file)}",
            }
        )
    synthetic = " (SYNTHETIC DATA)" if context["synthetic"] else ""
    titles = dict.fromkeys(f"{e['sourceSystem']} — cut of {e['cutDate']}" for e in context.get("extractions", []))
    sources = [{"title": title} for title in titles]
    return {
        "$schema": "https://datapackage.org/profiles/2.0/datapackage.json",
        "name": f"{product['id']}-{ed['period']}",
        "title": f"{product['name']} — {ed['period_long']}{synthetic}",
        "description": " ".join(product["description"]["purpose"].split()),
        "version": ed["version"],
        "created": ed["generated_at"],
        "keywords": product.get("tags", []),
        "licenses": [series_config["license"]],
        "sources": sources or [{"title": "No extraction recorded"}],
        "contributors": [{"title": f"{context['curator']} (role)", "roles": ["publisher"]}],
        "resources": resources,
        "x-statera": {
            "product": product["id"],
            "urn": contracts.custom_property_of(product, "urn"),
            "period": ed["period"],
            "cutDate": ed["cut_date"],
            "codeVersion": ed["code_version"],
            "run": context.get("run"),
            "extractions": context.get("extractions", []),
            "cuts": context.get("cuts", {}),
            "references": context.get("references", []),
            "series": series_config["series"],
            "corrects": ed.get("corrects"),
            "synthetic": context["synthetic"],
        },
    }


def to_ckan(datapackage: dict, product: dict, series_config: dict) -> dict:
    """Converts the Data Package into the CKAN `package_create` payload (open data portal)."""
    destination = series_config.get("open_data_portal", {"organization": "example-organization"})
    x = datapackage["x-statera"]
    return {
        "name": datapackage["name"],
        "title": datapackage["title"],
        "notes": datapackage["description"],
        "license_id": "cc-by",
        "owner_org": destination["organization"],
        "tags": [{"name": t} for t in datapackage.get("keywords", [])],
        "resources": [
            {
                "name": r.get("title", r["name"]),
                "description": r.get("description", ""),
                "format": r["format"].upper(),
                "url": f"<URL after upload>/{r['path']}",
                "hash": r["hash"],
            }
            for r in datapackage["resources"]
        ],
        "extras": [
            {"key": "period", "value": x["period"]},
            {"key": "cut_date", "value": x["cutDate"]},
            {"key": "frequency", "value": contracts.custom_property_of(product, "frequency")},
            {"key": "dataset", "value": destination.get("dataset", product["id"])},
            {"key": "urn", "value": x.get("urn") or ""},
        ],
    }


def curator(unit: Unit) -> str:
    owners = unit.read("1-specify/owners.yaml") or {}
    return governance.role_name(owners.get("curator", ""), unit.project)
