"""Phase 4 — Collect, and the link between products by CUT.

Implementations used in the processes:
    py:statera.collect.step      incremental extraction of a source-aligned product (task)
    py:statera.collect.pin_cut   waits for and pins the cut of the input internal products
                                 (intermediate message event, `statera.awaits`)

The cut manifest of an internal product lives in `<product>/7-disseminate/data/<date>/cut.json`
and is written by `statera.disseminate.mark_cut`.
"""

from __future__ import annotations

import json
from datetime import date

from statera import openlineage, ports
from statera.collect import extraction
from statera.edition import Edition
from statera.project import Unit


def cut_file(unit: Unit, cut_date: str):
    return unit.data(7) / cut_date / "cut.json"


def read_cut(unit: Unit, cut_date: str) -> dict | None:
    path = cut_file(unit, cut_date)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def released_cuts(unit: Unit) -> list[str]:
    return sorted(p.parent.name for p in unit.data(7).glob("*/cut.json"))


def _sources(manifests: dict) -> list[dict]:
    """Extractions (source, batch, rows, hash) from nested manifests: direct source or via an aggregate."""
    output = []
    for m in manifests.values():
        for table, f in (m.get("sources") or {}).items():
            output.append({"source": table, **f})
        output += _sources(m.get("inputs") or {})
    return output


def cut_extractions(manifests: dict) -> list[dict]:
    seen_pairs = {}
    for f in _sources(manifests):
        seen_pairs[(f["source"], f["cutDate"])] = {
            "source": f["source"], "sourceSystem": f["sourceSystem"], "cutDate": f["cutDate"],
            "batch": f["batch"], "rows": f["rows"], "sha256": f["sha256"],
        }  # fmt: skip
    return list(seen_pairs.values())


def cut_batches(manifests: dict) -> dict[str, int]:
    """{bronze table: maximum batch} for the `statera.at_cut` filter of the staging models."""
    return {f["source"]: f["batch"] for f in _sources(manifests)}


def step(ed: Edition) -> str:
    """GSBPM 4.3 — incremental extraction of the source-aligned product's sources into bronze."""
    sources = extraction.sources(ed.project, ed.unit)
    if not sources:
        ed.failures.append(f"{ed.product}: no bronze contract with customProperties.extraction")
        return "failed"
    ed.ctx["inputs"] = [openlineage.dataset(f.origin, f"system://{f.system}") for f in sources]
    ed.ctx["outputs"] = [openlineage.dataset(f"bronze.{f.table}") for f in sources]
    for m in extraction.extract_increment(date.fromisoformat(ed.cut_date), sources):
        if m["batch"] is None:
            ed.log(f"  bronze {m['source']}: cut within the watermark ({m['watermark']}); no new batch")
        else:
            ed.log(
                f"  bronze {m['source']}: batch {m['batch']} with {m['rows']} rows "
                f"({m['window_start'] or 'beginning'} to {m['window_end']})"
            )
    extraction.publish_bronze_to_warehouse(sources, ed.project)
    ed.cuts = {}
    return "ok"


def pin_cut(ed: Edition) -> bool:
    """Waits for the cuts of the input internal products; when all exist, pins them in the edition."""
    inputs = ports.input_products(ed.unit)
    missing_products, manifests = [], {}
    for unit in inputs:
        manifest = read_cut(unit, ed.cut_date)
        if manifest is None:
            missing_products.append(unit.name)
        else:
            manifests[unit.name] = manifest
    if missing_products:
        ed.log(f"  cut {ed.cut_date} not yet released by: {', '.join(missing_products)}")
        return False
    ed.cuts = manifests
    ed.extractions = cut_extractions(manifests)
    if sources := extraction.sources(ed.project):
        target = {f.table for f in sources} & set(cut_batches(manifests))
        extraction.publish_bronze_to_warehouse([f for f in sources if f.table in target], ed.project)
    pinned = ", ".join(f"{name} (run {m['run'][:8]})" for name, m in manifests.items())
    ed.log(f"  cut {ed.cut_date} pinned: {pinned or 'no input internal product'}")
    return True
