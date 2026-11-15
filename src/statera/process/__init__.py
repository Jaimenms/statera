"""Phase 5 — Process. Process task: `implementation: dbt:build` (with the list of `models`).

`models` are dbt selectors used as they are:
  - `+model` builds the model and everything upstream (the aggregate product builds the staging
    models of the sources and the reference tables);
  - `model` builds only the model: a consumer-aligned product does NOT rebuild the internal product it
    depends on, it reads the already released cut. The executor adds only the needed reference tables (seeds).
The edition variables go to dbt: cut-off date, reference month and the batches of each source's cut.
Test failures do NOT block here: they are control results, evaluated in the next task (GSBPM
6.2), which decides the path at the gateway.
"""

from __future__ import annotations

from statera import contracts, openlineage
from statera.edition import Edition
from statera.process import dbt, rules


def _events_by_model(ed: Edition, manifest: dict, results: list[dict]) -> None:
    """OpenLineage events (START/COMPLETE) per dbt model run, from the manifest."""
    nodes = {**manifest["nodes"], **manifest["sources"]}

    def name(uid: str) -> str:
        node = nodes[uid]
        return f"{node['schema']}.{node.get('alias') or node.get('identifier') or node['name']}"

    for r in results:
        uid = r["unique_id"]
        if not uid.startswith("model.") or r["status"] != "success":
            continue
        node = manifest["nodes"][uid]
        inputs = [openlineage.dataset(name(d)) for d in node["depends_on"]["nodes"] if d in nodes]
        run_id = openlineage.new_run_id()
        for kind in ("START", "COMPLETE"):
            ev = openlineage.event(
                kind, f"dbt.{node['name']}", run_id, inputs, [openlineage.dataset(name(uid))], ed.facet, ed.parent
            )
            openlineage.emit(ev, ed.events_file)


def dbt_step(ed: Edition, models: list[str]) -> str:
    project = ed.project
    pending_items = dbt.outdated(project)
    if pending_items or not rules.is_synced(project):
        ed.failures.append("Generated dbt files are outdated: run `statera dbt generate` and `statera rules sync`")
        return "failed"
    dbt.ensure_packages(project)
    from statera.collect import cut_batches

    selector = []
    if models:
        seeds = [f"+{m},resource_type:seed" for m in models if not m.startswith("+")]
        selector = ["--select", *models, *seeds]
    variables = {"cut_date": ed.cut_date, "period": ed.period, "batches": cut_batches(ed.cuts)}
    process = dbt.run(["build", *selector], variables)
    results = dbt.results(project)
    if process.returncode != 0 and not results:
        ed.failures.append(f"dbt build did not run:\n{process.stdout[-2000:]}")
        return "failed"
    total, failing = dbt.test_count(results)
    ed.dbt_results = {
        "tests": total,
        "failed_tests": failing,
        "failures": dbt.failures(results),
        "nodes": [r["unique_id"] for r in results],
    }
    manifest = dbt.manifest(generate=False, project=project)
    _events_by_model(ed, manifest, results)
    outputs = [c for path, c in contracts.all_(project).values() if project.unit_of(path) == ed.unit]
    ed.ctx["outputs"] = [openlineage.dataset(contracts.physical_table(c)) for c in outputs]
    summary = process.stdout.strip().splitlines()[-1].split("Done. ")[-1] if process.stdout.strip() else ""
    ed.log(f"  dbt build: {summary}")
    return "ok"
