"""Publication series and edition-by-edition register (GSBPM 7).

Two different things, kept in different places:

* **Series** (stable, versioned in the content repository): `<product>/7-disseminate/series.yaml`
  describes what each edition publishes, the license, the revision and correction policies, the main
  port and where the editions are recorded. It only changes when the series changes.

* **Editions** (grow every period, OUTSIDE the content repository): in the register declared in
  statera.toml ([registry] editions), one `datapackage.json` per reference month and an
  `editions.csv` index. In production, another git repository written by automatic commit, or
  versioned storage. Publishing an edition does not require a commit to the code.
"""

from __future__ import annotations

import csv
import json
from functools import cache
from pathlib import Path

import pandas as pd
from jsonschema import Draft7Validator

from statera.project import STANDARD_SCHEMAS, Project, Unit, current, read_yaml

INDEX_COLUMNS = [
    "period",
    "version",
    "cut_date",
    "published_at",
    "code_version",
    "run",
    "files",
    "suppressed_cells",
    "corrects_version",
    "main_resource",
    "main_resource_sha256",
]


def _root_readme(project: Project) -> str:
    return f"""# Register of published editions — {project.organization or project.name}

This repository is written **by the production process** (publication task of the to-be process,
run by `statera`), by automatic commit, for each published edition. Do not edit it by hand. The
code, the contracts and the design of the series live in the content repository (`{project.root.name}`);
only the history of editions lives here:

| File | Content |
|---|---|
| `<series>/<period>.datapackage.json` | Data Package of the edition: files, hashes, cut-off date |
| `<series>/editions.csv` | Series index: one row per edition (and per correction) |
| `<series>/evaluations/<period>.json` and `.md` | Quality indicators of the edition (GSBPM 8) |
| `<series>/README.md` | Generated page: editions, revisions, lineage per version, duration |

The published data files live in the publication storage (open data portal); this repository
holds the metadata and the hashes that prove what was published.
""" + ("\n> Demonstration with SYNTHETIC DATA.\n" if project.synthetic else "")


@cache
def _datapackage_validator() -> Draft7Validator:
    return Draft7Validator(json.loads((STANDARD_SCHEMAS / "datapackage-2.0.json").read_text()))


def series_file(unit: Unit) -> Path:
    return unit.phase(7) / "series.yaml"


def series(project: Project | None = None) -> list[tuple[Unit, dict]]:
    project = project or current()
    return [(u, read_yaml(series_file(u))) for u in project.products() if u.has("7-disseminate/series.yaml")]


def series_config(unit: Unit) -> dict:
    return read_yaml(series_file(unit))


def edition_path(unit: Unit, period: str) -> Path:
    default = series_config(unit)["edition_registry"]["edition"]
    return unit.project.edition_registry / default.format(period=period)


def index_path(unit: Unit) -> Path:
    return unit.project.edition_registry / series_config(unit)["edition_registry"]["index"]


def evaluations_folder(unit: Unit) -> Path:
    return index_path(unit).parent / "evaluations"


def editions(unit: Unit) -> list[dict]:
    """Recorded Data Packages of the series, in reference month order."""
    folder = edition_path(unit, "X").parent
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.datapackage.json"))]


def is_published(unit: Unit, period: str) -> bool:
    return edition_path(unit, period).exists()


def version_and_correction(unit: Unit, period: str) -> tuple[str, dict | None]:
    """Version of the new record; if the edition already exists, it is a correction and the version goes up."""
    existing = edition_path(unit, period)
    if not existing.exists():
        return "1.0.0", None
    previous = json.loads(existing.read_text(encoding="utf-8"))
    major, minor, _ = (int(x) for x in previous["version"].split("."))
    return f"{major}.{minor + 1}.0", {"version": previous["version"], "publishedAt": previous["created"]}


def register(unit: Unit, datapackage: dict) -> Path:
    errors = [e.message for e in _datapackage_validator().iter_errors(datapackage)]
    if errors:
        raise ValueError(f"Invalid Data Package, edition not recorded: {errors[:3]}")
    root = unit.project.edition_registry
    root.mkdir(parents=True, exist_ok=True)
    if not (root / "README.md").exists():
        (root / "README.md").write_text(_root_readme(unit.project), encoding="utf-8")
    destination = edition_path(unit, datapackage["x-statera"]["period"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(datapackage, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    rebuild_index(unit)
    return destination


def _index_row(dp: dict, principal: str) -> dict:
    x = dp["x-statera"]
    resource = next((r for r in dp["resources"] if r["name"] == principal), {})
    return {
        "period": x["period"],
        "version": dp["version"],
        "cut_date": x["cutDate"],
        "published_at": dp["created"],
        "code_version": x["codeVersion"],
        "run": x.get("run") or "",
        "files": len(dp["resources"]),
        "suppressed_cells": sum((r.get("x-statera") or {}).get("suppressedCells", 0) for r in dp["resources"]),
        "corrects_version": (x.get("corrects") or {}).get("version", ""),
        "main_resource": principal,
        "main_resource_sha256": resource.get("hash", "").removeprefix("sha256:"),
    }


def rebuild_index(unit: Unit) -> Path:
    destination = index_path(unit)
    destination.parent.mkdir(parents=True, exist_ok=True)
    principal = series_config(unit)["main_resource"]
    with destination.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=INDEX_COLUMNS, lineterminator="\n")  # LF, as .gitattributes
        writer.writeheader()
        writer.writerows(_index_row(dp, principal) for dp in editions(unit))
    return destination


def previous_published_period(unit: Unit, period: str) -> str | None:
    previous_periods = [dp["x-statera"]["period"] for dp in editions(unit) if dp["x-statera"]["period"] < period]
    return previous_periods[-1] if previous_periods else None


def published_table(unit: Unit, period: str, port: str) -> pd.DataFrame | None:
    """Table of a port as published in the edition (phase 7 publication folder)."""
    name = series_config(unit)["files_per_edition"]["tables"].format(port=port)
    path = unit.data(7) / period / name
    return pd.read_csv(path, dtype={unit.project.conventions["period_column"]: str}) if path.exists() else None


def previous_table(unit: Unit, period: str, port: str) -> pd.DataFrame | None:
    """The same port in the immediately previous published edition (controls and summary)."""
    previous = previous_published_period(unit, period)
    return published_table(unit, previous, port) if previous else None
