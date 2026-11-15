"""Phase 7 — Disseminate. Process tasks:

    py:statera.disseminate.step         statistical disclosure control, gate and package generation (6.4 and 7.2)
    py:statera.disseminate.publish      recording of the approved edition in the editions register (7.3)
    py:statera.disseminate.mark_cut     INTERNAL products: releases the cut to the consumers (7.3)

The package is generated before human review and approval; only the APPROVED edition is recorded. An
internal product does not publish files: it releases a cut, with an immutable manifest.
"""

from __future__ import annotations

import json
import shutil

from statera import contracts, openlineage, ports, references
from statera.analyse.disclosure_control import (
    ClassificationError,
    apply_disclosure_control,
    check_publication,
)
from statera.collect import cut_file, extraction
from statera.disseminate import dissemination, registry
from statera.edition import Edition, EditionFailure, now
from statera.process import dbt


def _context(ed: Edition, version: str, corrects: dict | None) -> dict:
    series_config = registry.series_config(ed.unit)
    principal = series_config["main_resource"]
    control = next((c for c in (ed.revisions or {}) if c), None)
    revisions = (ed.revisions or {}).get(control, []) if control else []
    return {
        "product": ports.odps(ed.unit),
        "edition": {
            "period": ed.period,
            "period_long": dissemination.period_long(ed.period),
            "cut_date": ed.cut_date,
            "version": version,
            "corrects": corrects,
            "generated_at": now(),
            "code_version": dissemination.code_version(ed.project.root),
        },
        "revisions": revisions,
        "main_resource": principal,
        "run": ed.run_id,
        "extractions": ed.extractions,
        "cuts": {name: {"cutDate": m["cutDate"], "run": m["run"], "urn": m.get("urn")} for name, m in ed.cuts.items()},
        "references": references.used(ed.unit, dbt.manifest(generate=False, project=ed.project), ed.cuts),
        "curator": dissemination.curator(ed.unit),
        "synthetic": ed.project.synthetic,
    }


def step(ed: Edition) -> str:
    """Generates the edition package in <product>/7-disseminate/data/<period>/."""
    unit = ed.unit
    if registry.is_published(unit, ed.period) and not ed.force:
        raise EditionFailure(
            f"Edition {ed.period} already published ({registry.edition_path(unit, ed.period)}). "
            "Publications are not recomputed; use --force to issue a correction."
        )
    tables = dissemination.load_tables(unit)
    try:
        for t in tables:
            apply_disclosure_control(t)
        check_publication(tables, unit.read("1-specify/classification.yaml") or {})
    except ClassificationError as error:
        raise EditionFailure(str(error)) from error

    folder = ed.data_folder(7)
    if folder.exists():
        shutil.rmtree(folder)  # package not yet recorded: it can be redone
    folder.mkdir(parents=True)
    version, corrects = registry.version_and_correction(unit, ed.period)
    context = _context(ed, version, corrects)
    series_config = registry.series_config(unit)
    files = series_config["files_per_edition"]
    for t in tables:
        t.data.to_csv(folder / files["tables"].format(port=t.port), index=False, lineterminator="\n")
    xlsx = folder / files["spreadsheet"].format(period=ed.period)
    dissemination.generate_xlsx(xlsx, [t for t in tables if t.in_spreadsheet], context)  # microdata only in CSV
    summary_md = folder / files["summary"]
    summary_md.write_text(dissemination.generate_summary(unit, "summary.md.j2", tables, context), encoding="utf-8")

    datapackage = dissemination.generate_datapackage(folder, tables, [xlsx, summary_md], context, series_config)
    (folder / "datapackage.json").write_text(
        json.dumps(datapackage, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    ckan = dissemination.to_ckan(datapackage, context["product"], series_config)
    (folder / "ckan.json").write_text(json.dumps(ckan, indent=2, ensure_ascii=False), encoding="utf-8")

    ed.publication = {
        "folder": str(folder),
        "version": version,
        "corrects": corrects,
        "suppressed_cells": {t.port: t.suppressed_cells for t in tables if t.applied_control},
        "removed_rows": {t.port: t.removed_rows for t in tables if t.removed_rows},
    }
    ed.ctx["outputs"] = [
        openlineage.dataset(f"{ed.product}/{ed.period}/{r['path']}", "file://publication")
        for r in datapackage["resources"]
    ]
    suppressed = sum(ed.publication["suppressed_cells"].values())
    removed_count = sum(ed.publication["removed_rows"].values())
    extra = f"; {removed_count} records removed by k-anonymity" if removed_count else ""
    ed.log(f"  package: {ed.project.rel(folder)} (version {version}; {suppressed} cells suppressed{extra})")
    return "ok"


def publish(ed: Edition) -> str:
    """Records the approved edition (Data Package and evaluation) in the editions register."""
    unit = ed.unit
    folder = ed.data_folder(7)
    datapackage = json.loads((folder / "datapackage.json").read_text(encoding="utf-8"))
    destination = registry.register(unit, datapackage)
    evaluations = registry.evaluations_folder(unit)
    evaluations.mkdir(parents=True, exist_ok=True)
    for file in ed.data_folder(8).glob("evaluation.*"):
        shutil.copy2(file, evaluations / f"{ed.period}{file.suffix}")
    from statera import documentation  # local import: documentation depends on the register

    page = documentation.generate_registry(unit)
    ed.log(f"  registry: {ed.project.rel(destination)} (series page: {ed.project.rel(page)})")
    return "ok"


def _counts_at_cut(ed: Edition) -> dict:
    """Rows of each output of the internal product in the cut's partition."""
    import duckdb

    output = {}
    con = duckdb.connect(str(ed.project.warehouse), read_only=True)
    try:
        for port in ports.output_ports(ed.unit):
            contract = contracts.all_(ed.project)[port["contractId"]][1]
            table, partition = (
                contracts.physical_table(contract),
                contracts.custom_property_of(contract, "partitionedBy"),
            )
            if partition:
                rows = con.execute(
                    f"SELECT count(*) FROM {table} WHERE {partition} = CAST(? AS DATE)", [ed.cut_date]
                ).fetchone()[0]
                output[port["name"]] = {"contract": contract["id"], "table": table, "rows": rows}
    finally:
        con.close()
    return output


def mark_cut(ed: Edition) -> str:
    """Internal product: writes the cut's immutable manifest and releases it to the authorized consumers."""
    from datetime import date

    destination = cut_file(ed.unit, ed.cut_date)
    if destination.exists() and not ed.force:
        raise EditionFailure(f"Cut {ed.cut_date} of {ed.product} already released; cuts are immutable (use --force)")
    manifest = {
        "product": ed.product,
        "urn": ed.unit.urn,
        "alignment": ed.unit.alignment,
        "cutDate": ed.cut_date,
        "run": ed.run_id,
        "markedAt": now(),
        "codeVersion": dissemination.code_version(ed.project.root),
        "authorizedConsumers": sorted(ports.authorized_consumers(ed.unit)),
    }
    sources = extraction.sources(ed.project, ed.unit)
    if sources:
        manifest["sources"] = extraction.source_manifest(date.fromisoformat(ed.cut_date), sources)
    else:
        manifest["outputs"] = _counts_at_cut(ed)
        manifest["references"] = references.used(ed.unit, dbt.manifest(generate=False, project=ed.project), ed.cuts)
    manifest["inputs"] = ed.cuts
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    summary = manifest.get("sources") or manifest.get("outputs")
    details = ", ".join(
        f"{name}: {v['rows']} rows" + (f" up to batch {v['batch']}" if "batch" in v else "")
        for name, v in summary.items()
    )
    ed.log(f"  cut {ed.cut_date} released ({details})")
    return "ok"
