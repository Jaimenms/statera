"""Phase 6 — Analyse. Process task: `implementation: py:statera.analyse.step`.

Gathers ALL control results of the edition and decides the status the gateway uses:
  - dbt tests (data, CK-* SQL controls, business rule unit tests), run in the previous task;
  - checks of the ODCS contracts of the built tables (and of the input bronze);
  - controls declared in `6-analyse/controls.yaml` (comparison with the previous edition).
Results go to `<product>/6-analyse/data/<period>/`.
"""

from __future__ import annotations

import json

from statera import contracts, openlineage, ports
from statera.analyse import controls, verification
from statera.disseminate import registry
from statera.edition import Edition, now


def contracts_in_scope(ed: Edition) -> set[str]:
    """Contracts checked in the edition: the product's own outputs and the assets built in this
    run (staging and reference tables included). Inputs from internal products are not
    re-checked: the producer checked them when releasing the cut."""
    project = ed.project
    nodes = set(ed.dbt_results.get("nodes", []))
    scope = set()
    for cid, (path, contract) in contracts.all_(project).items():
        if project.unit_of(path) == ed.unit:
            scope.add(cid)
            continue
        node = contracts.dbt_node(contract)
        if node and node[0] in ("model", "seed") and f"{node[0]}.{project.dbt_name}.{node[1]}" in nodes:
            scope.add(cid)
    return scope


def _save(ed: Edition, name: str, lines: list[dict]) -> None:
    folder = ed.data_folder(6)
    folder.mkdir(parents=True, exist_ok=True)
    content = {**ed.facet, "registered_at": now(), "results": lines}
    (folder / f"{name}.json").write_text(
        json.dumps(content, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )


def _rows(results: list[verification.Result]) -> list[dict]:
    return [
        {
            "contract": r.contract,
            "verification": r.verification,
            "ok": bool(r.ok),
            "detail": r.detail,
            "severity": r.severity,
        }
        for r in results
    ]


def step(ed: Edition) -> str:
    """GSBPM 6.2 — validate outputs. Returns 'failed' if any blocking control failed."""
    scope = contracts_in_scope(ed)
    own_contracts = [c for path, c in contracts.all_(ed.project).values() if ed.project.unit_of(path) == ed.unit]
    ed.ctx["inputs"] = [openlineage.dataset(contracts.physical_table(c)) for c in own_contracts]

    checks = verification.check_all(scope, ed.project, ed.cut_date)
    _save(ed, "contracts", _rows(checks))
    definitions = (ed.unit.read("6-analyse/controls.yaml") or {}).get("controls", [])
    control_inputs = controls.Inputs(
        ed.period,
        current=lambda port: ports.read_port(ed.unit, port),
        previous=lambda port: registry.previous_table(ed.unit, ed.period, port),
        period_column=ed.project.conventions["period_column"],
    )
    control_results, tables = controls.run(definitions, control_inputs)
    _save(ed, "controls", _rows(control_results))
    dbt_failures = ed.dbt_results.get("failures", [])
    _save(ed, "dbt-tests", [{"failed": f} for f in dbt_failures])

    ed.revisions = tables or None
    ed.checks = len(checks)
    ed.non_blocking_failures = sum(not r.ok and not r.blocking for r in checks)
    ed.log(f"  ODCS contracts: {len(checks)} checks on {len(scope)} contracts, at cut {ed.cut_date}")
    for r in control_results:
        mark = "ok" if r.ok else ("ERROR" if r.blocking else "warning")
        ed.log(f"  [{mark}] {r.verification}: {r.detail}")
    blockers = [f"dbt: {f}" for f in dbt_failures]
    blockers += [f"{r.contract} {r.verification}: {r.detail}" for r in [*checks, *control_results] if r.blocking]
    if blockers:
        ed.failures += blockers
        for b in blockers[:10]:
            ed.log(f"  FAIL {b}")
        return "failed"
    return "ok"
