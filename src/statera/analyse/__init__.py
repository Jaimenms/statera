"""Phase 6 — Analyse. Process task: `implementation: Gate` (statera.extend), with `checks: [CK-..]`.

Gathers ALL control results of the edition and decides the status the gateway uses:
  - dbt tests (data, CK-* SQL controls, business rule unit tests), run in the previous task;
  - checks of the ODCS contracts of the built tables (and of the input bronze);
  - controls declared in `6-analyse/controls.yaml` (comparison with the previous edition).
Results go to `<product>/6-analyse/data/<period>/`.
"""

from __future__ import annotations

import json

from statera import contracts, openlineage, ports
from statera.analyse import verification
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


def _run_checks(ed: Edition, check_ids: list[str] | None) -> tuple[list[verification.Result], dict, list[dict]]:
    """The checks of the gate (controls.yaml entries, by id; all of them when the process lists none)."""
    import duckdb

    from statera.extend import registry as extensions
    from statera.extend.base import CheckContext, CheckResult

    definitions = (ed.unit.read("6-analyse/controls.yaml") or {}).get("controls", [])
    if check_ids is not None:
        by_id = {d["id"]: d for d in definitions}
        definitions = [by_id[i] for i in check_ids if i in by_id]  # unknown ids: validation reports them
    results, tables, details = [], {}, []
    for definition in definitions:
        label = f"{definition['id']} {definition.get('name') or definition.get('check') or definition.get('type')}"
        try:
            cls = extensions.check_class(ed.unit, definition)
            severity = definition.get("severity", cls.severity)
            context = CheckContext(
                ed.product, ed.period, definition,
                current=lambda port: ports.read_port(ed.unit, port),
                previous=lambda port: registry.previous_table(ed.unit, ed.period, port),
                period_column=ed.project.conventions["period_column"], log=ed.log,
            )  # fmt: skip
            outcome = cls().evaluate(context)
        except (duckdb.Error, KeyError, extensions.ExtensionError) as error:  # e.g. a table dbt did not build
            severity, outcome = "error", CheckResult.fail(f"not run: {str(error).splitlines()[0]}")
        ok = outcome.verdict == "ok"
        level = "info" if ok else ("warning" if outcome.verdict == "warning" else severity)
        results.append(verification.Result("control", label, ok, outcome.message, level))
        details.append({"control": definition["id"], "verdict": outcome.verdict, "severity": level,
                        "message": outcome.message, "rows": outcome.rows})  # fmt: skip
        if outcome.table is not None:
            tables[definition["id"]] = outcome.table
    return results, tables, details


def gate(ed: Edition, check_ids: list[str] | None = None) -> str:
    """GSBPM 6.2 — validate outputs: contracts, dbt tests and the gate's checks. Returns `failed` when a
    contract, a dbt test or a check of severity error fails; `warning` when a check warns or one of severity
    warning fails; `ok` otherwise."""
    scope = contracts_in_scope(ed)
    own_contracts = [c for path, c in contracts.all_(ed.project).values() if ed.project.unit_of(path) == ed.unit]
    ed.ctx["inputs"] = [openlineage.dataset(contracts.physical_table(c)) for c in own_contracts]

    checks = verification.check_all(scope, ed.project, ed.cut_date)
    _save(ed, "contracts", _rows(checks))
    control_results, tables, details = _run_checks(ed, check_ids)
    _save(ed, "controls", details)
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
    return "warning" if any(not r.ok for r in control_results) else "ok"


def step(ed: Edition) -> str:
    """statera 0.5 form (`py:statera.analyse.step`): the Gate with every control; a warning is `ok`."""
    outcome = gate(ed)
    return "ok" if outcome == "warning" else outcome
