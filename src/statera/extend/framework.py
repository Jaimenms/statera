"""The framework's own steps, as classes of the extension API: what the processes name by default.

    Task   DbtBuild (5) · Extract (4.3) · Gate (4.4, 5.3, 6.2) · Package (6.4, 7.1-7.2) · Publish (7.3-7.5)
           · MarkCut (7.3) · Evaluate (8.1-8.2)
    Await  PinCut (the cut of the input internal products, 09)
    Check  RevisionBetweenEditions · Outlier (the control types of 06)

They work on the whole edition (the framework's privilege: `ctx._edition`); a product's classes see only the
public context. A product may override one by subclassing it with the same name.
"""

from __future__ import annotations

from statera.extend.base import Await, Check, CheckContext, CheckResult, Task, TaskContext, TaskResult


def _models(ctx: TaskContext) -> list[str]:
    models = (ctx._element.get("statera") or {}).get("models") or []
    return [m.strip() for m in models.split(",") if m.strip()] if isinstance(models, str) else list(models)


class DbtBuild(Task):
    """Builds the models listed in the process (`models`), with everything upstream of them (dbt build)."""

    gsbpm = "5.1-5.8"

    def run(self, ctx: TaskContext) -> TaskResult:
        from statera.process import dbt_step

        return TaskResult.of(dbt_step(ctx._edition, _models(ctx)))


class Extract(Task):
    """Incremental extraction of a source-aligned product's bronze contracts, in batches (GSBPM 4.3)."""

    gsbpm = "4.3"

    def run(self, ctx: TaskContext) -> TaskResult:
        from statera import collect

        return TaskResult.of(collect.step(ctx._edition))


class Gate(Task):
    """Runs the edition's controls and decides the path: contract verifications, the dbt test results and
    the checks listed in the process (`checks`, ids in 6-analyse/controls.yaml; all of them when the process
    lists none). `failed` when a check of severity error fails, `warning` when a check warns or one of severity
    warning fails, `ok` otherwise. A gateway with no `warning` flow follows the `ok` one."""

    gsbpm = "4.4;5.3;6.2"
    outcomes = frozenset({"ok", "warning", "failed"})

    def run(self, ctx: TaskContext) -> TaskResult:
        from statera.analyse import gate

        return TaskResult.of(gate(ctx._edition, (ctx._element.get("statera") or {}).get("checks")))


class Package(Task):
    """Statistical disclosure control and the edition package: CSV, spreadsheet, summary, Data Package, CKAN."""

    gsbpm = "6.4;7.1;7.2"

    def run(self, ctx: TaskContext) -> TaskResult:
        from statera import disseminate

        return TaskResult.of(disseminate.step(ctx._edition))


class Publish(Task):
    """Records the approved edition in the editions register (the automatic part of the publication)."""

    gsbpm = "7.3-7.5"

    def run(self, ctx: TaskContext) -> TaskResult:
        from statera import disseminate

        return TaskResult.of(disseminate.publish(ctx._edition))


class MarkCut(Task):
    """Releases the cut of an internal product: its immutable manifest (09)."""

    gsbpm = "7.3"

    def run(self, ctx: TaskContext) -> TaskResult:
        from statera import disseminate

        return TaskResult.of(disseminate.mark_cut(ctx._edition))


class Evaluate(Task):
    """Quality indicators of the edition, with their alerts (06)."""

    gsbpm = "8.1-8.2"

    def run(self, ctx: TaskContext) -> TaskResult:
        from statera import evaluate

        return TaskResult.of(evaluate.step(ctx._edition))


class PinCut(Await):
    """The cuts of the input internal products are released: pins them for the edition (09)."""

    def ready(self, ctx: TaskContext) -> bool:
        from statera import collect

        return bool(collect.pin_cut(ctx._edition))


# ---------------------------------------------------------------------------
# Checks: the control types of 06, on top of statera.analyse.controls
# ---------------------------------------------------------------------------


def _as_inputs(ctx: CheckContext):
    from statera.analyse import controls

    return controls.Inputs(ctx.period, ctx.current, ctx.previous, period_column=ctx.period_column)


class RevisionBetweenEditions(Check):
    """Difference, by key, between this edition and the previous published one (port, key, measures,
    relative_base, warning_threshold, error_threshold): fails above the error threshold, warns above the
    warning one."""

    severity = "error"

    def evaluate(self, ctx: CheckContext) -> CheckResult:
        from statera.analyse import controls

        params = {"name": "revision", **ctx.params}
        results, table = controls._revision(params, _as_inputs(ctx))
        rows = [{"key": r.verification, "ok": r.ok, "detail": r.detail, "severity": r.severity} for r in results]
        flagged = [r for r in results if not r.ok]
        if not flagged:
            return CheckResult.ok(results[0].detail if table is None else f"{len(results)} key(s) within tolerance",
                                  rows=rows, table=table)  # fmt: skip
        message = f"{len(flagged)} key(s) revised beyond tolerance: " + "; ".join(r.detail for r in flagged[:3])
        if any(r.severity == "error" for r in flagged):
            return CheckResult.fail(message, rows=rows, table=table)
        return CheckResult.warn(message, rows=rows, table=table)


class Outlier(Check):
    """Value of the current reference month against the historical mean (port, measure, factor)."""

    severity = "warning"

    def evaluate(self, ctx: CheckContext) -> CheckResult:
        from statera.analyse import controls

        params = {"name": "outlier", **ctx.params}
        (result,), _ = controls._outlier(params, _as_inputs(ctx))
        return CheckResult.ok(result.detail) if result.ok else CheckResult.fail(result.detail)


TASKS = (DbtBuild, Extract, Gate, Package, Publish, MarkCut, Evaluate)
AWAITS = (PinCut,)
CHECKS = (RevisionBetweenEditions, Outlier)
FRAMEWORK: dict[str, type] = {c.__name__: c for c in (*TASKS, *AWAITS, *CHECKS)}

# `py:` functions and `dbt:build` of statera 0.5 and earlier: still accepted, run as these classes
LEGACY = {
    "dbt:build": "DbtBuild",
    "py:statera.collect.step": "Extract",
    "py:statera.collect.pin_cut": "PinCut",
    "py:statera.analyse.step": "Gate",
    "py:statera.disseminate.step": "Package",
    "py:statera.disseminate.publish": "Publish",
    "py:statera.disseminate.mark_cut": "MarkCut",
    "py:statera.evaluate.step": "Evaluate",
}
CONTROL_TYPES = {"revision-between-editions": "RevisionBetweenEditions", "outlier": "Outlier"}
