"""Running an edition: the product's to-be process IS the run plan.

The executor walks the process YAML (`<product>/2-design/processes/to-be.yaml`) from the start
event, following the flows:

  - automatic task         runs the `implementation` (py:<module>.<function> or dbt:build) and keeps
                           the returned status ("ok" or "failed"); emits OpenLineage and records the duration;
  - exclusive gateway      follows the flow whose `condition` equals the status of the last task;
  - wait event             (intermediateCatchEvent with `statera.awaits`) calls the function; while
                           it returns false, the edition stays "waiting" (e.g. the cut of the input
                           internal product has not been released yet); `statera resume` re-evaluates;
  - human task             stops and saves the state ("waiting"); `statera resume` completes the
                           task (approving or rejecting) and resumes from there. Approving after an
                           evaluation with alerts requires acknowledging them (`--acknowledge-alerts`).
                           In the demo, `--approve` simulates the human decisions;
  - exception task         (statera.exception: true) the edition becomes "blocked", with the failures;
  - end event              the edition is "done".

There is no sequence of steps in the code: changing the process changes the run, and the BPMN
generated from the same YAML documents exactly what runs (see specification,
05-processes-and-execution.md).
"""

from __future__ import annotations

import getpass
import importlib
import os
from collections import defaultdict

from statera import openlineage
from statera.design import bpmn, processes
from statera.edition import Edition, EditionFailure
from statera.project import Project, current

HUMAN_TYPES = ("manual", "semi-automatic")


class InvalidProcess(RuntimeError):
    """The process cannot be run as modeled."""


def _implementation(reference: str):
    module, _, function = reference.removeprefix("py:").rpartition(".")
    try:
        return getattr(importlib.import_module(module), function)
    except (ImportError, AttributeError) as error:
        raise InvalidProcess(f"implementation {reference} not found") from error


def _datasets(proc: dict, element: dict, key: str) -> list[dict]:
    """OpenLineage datasets from the BPMN data associations (data stores with an `asset`)."""
    from statera import contracts

    data = {d["id"]: d for d in proc.get("data", [])}
    by_id = contracts.all_()
    output = []
    for ref in element.get(key, []):
        asset = data.get(ref, {}).get("asset")
        if asset in by_id:
            output.append(openlineage.dataset(contracts.physical_table(by_id[asset][1])))
    return output


def _run_task(ed: Edition, proc: dict, e: dict) -> str:
    ext = e.get("statera", {})
    impl = ext.get("implementation", "")
    inputs, outputs = _datasets(proc, e, "inputs"), _datasets(proc, e, "outputs")
    ed.log(f"{e['id']} — {e['name']}")
    status, message = "ok", ""
    with openlineage.execution(f"{ed.product}.{e['id']}", inputs, outputs, ed.facet, ed.events_file, ed.parent) as ctx:
        ed.ctx = ctx
        try:
            if impl == "dbt:build":
                from statera.process import dbt_step

                status = dbt_step(ed, list(ext.get("models", [])))
            elif impl.startswith("py:"):
                status = _implementation(impl)(ed)
            else:
                raise InvalidProcess(f"{e['id']}: implementation '{impl}' in an unknown format")
        except EditionFailure as error:
            status, message = "failed", str(error)
            ed.failures.append(f"{e['id']}: {error}")
    ed.record_task(e["id"], status, ctx.get("duration_s"), **({"message": message[:300]} if message else {}))
    ed.save()
    return status


def _next_node(outputs: dict, e: dict, status: str) -> str:
    flows = outputs[e["id"]]
    if e["type"] in bpmn.GATEWAYS:
        chosen = [f for f in flows if f.get("condition") == status]
        if len(chosen) != 1:
            raise InvalidProcess(f"{e['id']}: no flow (or more than one) with condition '{status}'")
        return chosen[0]["to"]
    if len(flows) != 1:
        raise InvalidProcess(f"{e['id']}: {len(flows)} outgoing flows outside a gateway (parallelism not supported)")
    return flows[0]["to"]


def _walk(ed: Edition, proc: dict, node: str, status: str, approve: bool) -> Edition:
    elements = {e["id"]: e for e in proc["elements"]}
    outputs: dict[str, list[dict]] = defaultdict(list)
    for f in proc["flows"]:
        outputs[f["from"]].append(f)
    for _ in range(10 * len(elements)):  # guard against loops with no exit
        e = elements[node]
        ext = e.get("statera", {})
        if e["type"] == "endEvent":
            ed.status, ed.pending = "done", None
            _end_edition(ed, "COMPLETE")
            ed.log(f"{ed.label}: done ({e['name']}).")
            break
        if e["type"] in bpmn.TASKS and ext.get("exception"):
            ed.status, ed.pending = "blocked", e["id"]
            _end_edition(ed, "FAIL")
            ed.log(f"{ed.label} BLOCKED: awaiting '{e['name']}' ({_role(proc, e)}).")
            break
        if ext.get("awaits") and e["type"] not in bpmn.TASKS:
            if not _await(ed, e):
                ed.status, ed.pending = "waiting", e["id"]
                ed.log(f"Awaiting '{e['name']}'. Release the input and run: statera resume")
                break
        elif e["type"] in bpmn.TASKS and ext.get("type") in HUMAN_TYPES:
            if not approve:
                ed.status, ed.pending = "waiting", e["id"]
                ed.log(f"Awaiting '{e['name']}' ({_role(proc, e)}). Complete it with: statera resume")
                break
            status = _complete_human_task(ed, e, "approved", simulated=True)
        elif e["type"] in bpmn.TASKS:
            status = _run_task(ed, proc, e)
            if (
                status == "failed"
                and outputs[e["id"]]
                and elements[outputs[e["id"]][0]["to"]]["type"] not in bpmn.GATEWAYS
            ):
                ed.status, ed.pending = "blocked", e["id"]
                _end_edition(ed, "FAIL")
                ed.log(f"{ed.label} BLOCKED at '{e['name']}' (no exception path modeled after the task).")
                break
        node = _next_node(outputs, e, status)
    else:
        raise InvalidProcess("the process did not reach its end: loop with no exit")
    ed.save()
    return ed


def _await(ed: Edition, e: dict) -> bool:
    satisfied = bool(_implementation(e["statera"]["awaits"])(ed))
    if satisfied:
        ed.record_task(e["id"], "ok", None, event=True)
    ed.save()
    return satisfied


def _role(proc: dict, e: dict) -> str:
    return next((r["role"] for r in proc["lanes"] if r["id"] == e["lane"]), "?")


def _check_acknowledgement(ed: Edition, e: dict, decision: str, simulated: bool, note: str | None) -> None:
    """Approving a human task after an evaluation with alerts requires someone to acknowledge them, with a
    note; the acknowledgement goes to the edition's evaluation, which is published with it."""
    if decision != "approved" or not ed.alerts or ed.acknowledgement:
        return
    if not note and not simulated:
        alerts = "; ".join(f"{a['id']} {a['name']} = {a['formatted_value']} ({a['alert_text']})" for a in ed.alerts)
        raise EditionFailure(
            f"{ed.label}: the evaluation has {len(ed.alerts)} indicator(s) in alert ({alerts}). "
            f"Read {ed.project.rel(ed.data_folder(8) / 'evaluation.md')} and acknowledge them to approve: "
            f'statera resume --acknowledge-alerts "<why the edition goes on>" (or --reject)'
        )
    from statera.evaluate import evaluation

    by = os.environ.get("STATERA_ROLE") or getpass.getuser()
    evaluation.acknowledge(ed, note or "simulated approval (demo)", by, e["id"], simulated=simulated)
    ed.log(f"  alerts acknowledged by {by}: {ed.acknowledgement['note']}")


def _complete_human_task(
    ed: Edition, e: dict, decision: str, simulated: bool = False, acknowledgement: str | None = None
) -> str:
    """Completes a human task. If it has an implementation (semi-automatic), runs it."""
    ext = e.get("statera", {})
    _check_acknowledgement(ed, e, decision, simulated, acknowledgement)
    ed.log(f"{e['id']} — {e['name']}: {decision}{' (simulated)' if simulated else ''}")
    if ext.get("implementation") and decision == "approved":
        ed.ctx = {}
        _implementation(ext["implementation"])(ed)
    ed.record_task(e["id"], decision, None, human=True, simulated=simulated)
    return decision


def _end_edition(ed: Edition, kind: str) -> None:
    openlineage.emit(openlineage.event(kind, f"{ed.product}.edition", ed.run_id, [], [], ed.facet), ed.events_file)


def release_calendar(product, period: str) -> dict:
    editions = {e["period"]: e for e in product.read("1-specify/calendar.yaml")["editions"]}
    if period not in editions:
        raise EditionFailure(f"Period {period} not in calendar.yaml; pass --cut-date")
    return editions[period]


def run(
    product: str,
    period: str | None = None,
    cut_date: str | None = None,
    force: bool = False,
    approve: bool = False,
    project: Project | None = None,
    with_dependencies: bool = False,
) -> Edition:
    """Runs an edition from the start, through the product's to-be process.

    Internal product (source-aligned or aggregate): the "edition" is a cut; pass only the cut-off date.
    With `with_dependencies`, first releases the missing cuts of the input internal products.
    """
    project = project or current()
    unit = project.product(product)
    if unit.internal:
        if not cut_date:
            raise EditionFailure(f"{product} is an internal product: pass --cut-date")
        period = period or cut_date
    elif not period:
        raise EditionFailure(f"{product}: pass --period")
    cut_date = cut_date or release_calendar(unit, period)["cut_date"]
    if with_dependencies:
        ensure_dependencies(unit, cut_date, approve)
    proc = processes.load(unit.to_be_process)
    if errors := processes.validate_structure(proc):
        raise InvalidProcess("; ".join(errors))
    ed = Edition(product, period, cut_date, force)
    ed.run_id = openlineage.new_run_id()
    openlineage.emit(openlineage.event("START", f"{product}.edition", ed.run_id, [], [], ed.facet), ed.events_file)
    cut = "" if unit.internal else f" — cut-off date {cut_date}"
    ed.log(f"{ed.label}{cut} — process {project.rel(unit.to_be_process)}")
    start = next(e["id"] for e in proc["elements"] if e["type"] == "startEvent")
    return _walk(ed, proc, start, "ok", approve)


def resume(
    product: str,
    period: str,
    decision: str = "approved",
    approve: bool = False,
    acknowledgement: str | None = None,
) -> Edition:
    """Completes the pending human task (approved | rejected), or re-evaluates the wait event, and resumes.
    `acknowledgement`: note acknowledging the evaluation's alerts, required to approve when there are any."""
    unit = current().product(product)
    ed = Edition.load(unit, period)
    if ed.status != "waiting" or not ed.pending:
        raise EditionFailure(f"{ed.label} is not awaiting a human task or an event (status: {ed.status})")
    proc = processes.load(unit.to_be_process)
    element = next(e for e in proc["elements"] if e["id"] == ed.pending)
    ed.status = "running"
    if (element.get("statera") or {}).get("awaits") and element["type"] not in bpmn.TASKS:
        if not _await(ed, element):
            ed.status = "waiting"
            ed.log(f"Still awaiting '{element['name']}'.")
            ed.save()
            return ed
        status = "ok"
    else:
        status = _complete_human_task(ed, element, decision, acknowledgement=acknowledgement)
    outputs: dict[str, list[dict]] = defaultdict(list)
    for f in proc["flows"]:
        outputs[f["from"]].append(f)
    return _walk(ed, proc, _next_node(outputs, element, status), status, approve)


def state(product: str, period: str) -> Edition:
    return Edition.load(current().product(product), period)


def editions(unit) -> list[str]:
    """Periods (or, in an internal product, cut-off dates) with a run of the product, oldest first."""
    folder = unit.runs_folder
    return sorted(p.name.removesuffix(".state.json") for p in folder.glob("*.state.json")) if folder.is_dir() else []


def _step(unit, ed: Edition) -> tuple[str, str]:
    """(step, role) where the edition is: the pending element, or the last task run."""
    task = ed.pending or (ed.history[-1]["task"] if ed.history and ed.status not in ("done",) else None)
    if not task:
        return "—", "—"
    try:
        proc = processes.load(unit.to_be_process)
    except Exception:  # noqa: BLE001 - process changed or missing: show the id
        return task, "?"
    element = next((e for e in proc["elements"] if e["id"] == task), None)
    return (f"{element['name']} ({task})", _role(proc, element)) if element else (task, "?")


def overview(product: str | None = None, latest_only: bool = True) -> list[dict]:
    """One row per edition: status, the BPMN step it is at, the role that has to act, and the alerts.
    All products (the latest edition of each), or every edition of one product."""
    project = current()
    units = [project.product(product)] if product else project.products()
    rows = []
    for unit in units:
        keys = editions(unit)
        if not keys:
            rows.append({"product": unit.name, "internal": unit.internal, "edition": None, "status": "no runs"})
            continue
        for key in keys[-1:] if latest_only else keys:
            ed = Edition.load(unit, key)
            step, role = _step(unit, ed)
            rows.append({
                "product": unit.name, "internal": unit.internal, "edition": key, "status": ed.status,
                "step": step, "role": role, "alerts": len(ed.alerts),
                "acknowledged": bool(ed.acknowledgement), "blocking": any(a.get("blocking") for a in ed.alerts),
            })  # fmt: skip
    return rows


def ensure_dependencies(unit, cut_date: str, approve: bool = False) -> None:
    """Releases, in order, the missing cuts of the internal products the unit depends on.

    In production, the orchestrator does this (dependencies between assets). An already released cut
    is reused: two consumers on the same cut do not redo the extraction or the canonical layer.
    """
    from statera import ports
    from statera.collect import read_cut

    for input_ in ports.input_products(unit):
        ensure_dependencies(input_, cut_date, approve)
        if read_cut(input_, cut_date) is not None:
            print(f"[statera] cut {cut_date} of {input_.name} already released: reused", flush=True)
            continue
        ed = run(input_.name, cut_date=cut_date, approve=approve, project=unit.project)
        if ed.status != "done":
            detail = "\n  - ".join(ed.failures[:10])
            raise EditionFailure(f"Dependency {input_.name} {cut_date} {ed.status} at {ed.pending}:\n  - {detail}")
