# 05 — Processes and execution

## Format

Each product has **one process**, `<product>/2-design/process.yaml`: a YAML file mirroring BPMN 2.0
one-to-one (schema `process`), from which `statera sync` generates `2-design/process.bpmn` and the
diagrams in `docs/processes/<product>.svg` and `.html`. There is no separate as-is and to-be: the process
in the repository is the one the executor runs; the history of how it was done before is in git. A
repository of statera 0.5 (`2-design/processes/to-be.yaml`, with or without an `as-is.yaml`) still runs
its `to-be.yaml`, but validation fails on every file of the old `2-design/processes/` folder: `to-be.yaml`
moves to `2-design/process.yaml` (its old `.bpmn` is deleted and regenerated), and an `as-is.yaml` leaves
the product (e.g. to the documentation) or is merged into the process.

Element types use the names from the OMG specification (`startEvent`, `serviceTask`, `userTask`,
`exclusiveGateway`, `dataStoreReference`…); what belongs to the standard goes in the `statera` block of
each task:

| Attribute | Values |
|---|---|
| `type` | `automatic` · `semi-automatic` · `manual` |
| `gsbpm` | Sub-process(es): `4.3`, `5.1-5.7`, `6.4;7.2` |
| `durationMin` | Expected duration per edition (minutes) |
| `implementation` | A `Task` class name (see Extension classes); before 0.6, `py:<module>.<function>` or `dbt:build` (still accepted) |
| `models` | With `DbtBuild` (or `dbt:build`): models to build, with everything upstream of them |
| `exception` | `true` for tasks off the normal path |
| `awaits` | On an `intermediateCatchEvent`: an `Await` class that says whether the wait is over (e.g. `PinCut`, the cut released, 09); before 0.6, `py:<module>.<function>` (still accepted) |
| `params` | Parameters given to the implementation (`ctx.params`) |
| `checks` | On a gate: the controls (ids in `6-analyse/controls.yaml`) it runs; all of them when absent |

Flows leaving gateways **must** have a `condition` (in an executable process): the status of the
last task that makes the executor follow that flow. In the generated BPMN it becomes
`<conditionExpression xsi:type="tFormalExpression">${status == 'ok'}</conditionExpression>`, which
comes back to the YAML on import. Data stores may have an `asset` (contract id), used for the
task's lineage datasets.

## Execution

The product's process (`2-design/process.yaml`) is the execution plan:

| Element | The executor |
|---|---|
| `startEvent` | Starts the edition (OpenLineage run of the edition) |
| Automatic task | Runs the `Task` class, whose outcome (`ok`, `failed` or another the class declares) is the status; records duration and events |
| `exclusiveGateway` | Follows the flow whose `condition` is the status of the last task; after a gate with no `warning` flow, a `warning` follows the `ok` flow |
| Human task (manual or semi-automatic) | Stops and saves the state (`waiting`); `statera resume` completes it with `approved` (or `rejected`, with `--reject`); a semi-automatic task runs its implementation when completed. Approving after an evaluation with alerts requires `--acknowledge-alerts "<note>"` (06) |
| Intermediate event with `awaits` | Asks the `Await` class; if it is not yet time, the edition stays `waiting` on it, and `statera resume` re-evaluates |
| Task with `exception: true` | The edition becomes `blocked`, with the failures recorded |
| `endEvent` | The edition is `done` |

The state of each run is saved in `<product>/8-evaluate/data/runs/<period>.state.json`. `statera status`
lists where every product's latest edition (or cut) is: the pending BPMN element, the role of its lane
and the evaluation alerts; `--product` lists every edition of one product.

An automatic task that fails with no gateway after it also blocks the edition. Framework tasks:
`Extract` (4.3), `DbtBuild` (5), `Gate` (controls, 6.2), `Package` (6.4, 7.1-7.2), `Evaluate` (8.1-8.2) and
`Publish` (7.3-7.5). In an internal product, the run is a **cut** (`--cut-date` is given, not the
reference month), and its own steps are the `MarkCut` task and the consumer's `PinCut` await, 09. With
`--with-dependencies`, the run first releases the missing cuts in the input internal products and reuses
those already released. An organization adds its own steps as classes of the product (below).

The state is kept in `<product>/8-evaluate/data/runs/<reference-month>.state.json`; the task
log in `runs.jsonl`; the OpenLineage events in `events.jsonl`, all in the same folder.

## Extension classes

> **Status: implemented in 0.6.0.** The `py:` functions and `dbt:build` of 0.5 still run, as the framework
> classes below, with a deprecation warning for `py:`.

Every step of a process, the framework's and the products', is a **class** that extends one of four
abstractions. The class declares what it does (GSBPM sub-process, tables read and written, possible
outcomes); validation checks those declarations against the process and the contracts **without
running anything**, and the executor gives the class a context instead of the whole edition.

| Abstraction | BPMN element | Method | Lives in |
|---|---|---|---|
| `Task` | `serviceTask`; the automatic part of a semi-automatic `userTask` | `run(ctx) -> TaskResult` | `<phase>/tasks.py` |
| `Check` | none: it runs inside a `Gate` (a `Task`) | `evaluate(ctx) -> CheckResult` | `6-analyse/checks.py` |
| `Await` | `intermediateCatchEvent` | `ready(ctx) -> bool` | `<phase>/tasks.py` |
| `SyntheticSource` | none: it feeds tests and demos | `generate(ctx, fault) -> dict[str, int]` | `3-build/synthetic.py` |

The four classes, their contexts and results are the **public extension API**, exported by
`statera.extend`. From 0.6.0 it follows semantic versioning: a name is removed or a signature changes
only after one minor version in which the old form still works and raises a deprecation warning.

### Task

```python
from statera.extend import Task, TaskContext, TaskResult


class Deduplicate(Task):
    """Removes the records sent twice by the source system."""

    gsbpm = "5.3"
    inputs = ["bronze.records"]  # contract tables read (physical names)
    outputs = ["silver.records_unique"]  # contract tables written
    outcomes = {"ok", "failed"}  # the default; a task may declare others, e.g. "partial"

    def run(self, ctx: TaskContext) -> TaskResult:
        records = ctx.read("bronze.records")
        ...
        ctx.write("silver.records_unique", unique)
        return TaskResult.ok(rows=len(unique))
```

- `TaskContext` is read-only about the run (`product`, `period`, `cut_date`, `run_id`, `params` from the
  task's `statera.params` in the process) and gives `read(table)`, `write(table, df)`, `folder` (the
  task's phase `data/` folder) and `log(message)`. `read` and `write` **must** refuse tables the class
  did not declare: OpenLineage inputs and outputs come from the declarations, so lineage is right by
  construction.
- `TaskResult` carries the `outcome` (one of `outcomes`), a message and metrics recorded in
  `runs.jsonl`. An outcome outside `outcomes` fails the task. A raised exception is outcome `failed`
  with the message.
- A task **must** be idempotent for the same product, period and cut-off date: running it again gives
  the same result.
- Tables in the engine that come from SQL or from a transformation of other tables **should** be dbt
  models (phase 5); a `Task` is for what is not a model (extraction, packaging, publication, calls to
  other systems).

### Check and Gate

```python
from statera.extend import Check, CheckContext, CheckResult


class SeasonalityBreak(Check):
    """The month deviates from the same month in previous years beyond the tolerance."""

    severity = "warning"  # "error" blocks the edition; "warning" is recorded

    def evaluate(self, ctx: CheckContext) -> CheckResult:
        current, previous = ctx.current("by-uf"), ctx.previous("by-uf")
        ...
        return CheckResult.warn("SP deviates 34% from the 3-year mean", rows=deviating)
```

Checks are not nodes of the process: a **gate** is a `Task` (the framework's `Gate`, or a subclass)
followed by an exclusive gateway, and it runs a list of checks. A gate's outcomes are `ok`, `warning`
and `failed`: `failed` when any check of severity `error` fails, `warning` when any check warns or a
check of severity `warning` fails, `ok` otherwise. The framework's gate of phase 6 also runs the contract
verifications and reads the dbt test results (what `py:statera.analyse.step` did in 0.5).

Checks are configured in `6-analyse/controls.yaml`, by class and with their parameters:

```yaml
controls:
  - {id: CK-01, check: RevisionBetweenEditions, port: by-uf, key: [period, uf_abbr], measures: [records]}
  - {id: CK-02, check: SeasonalityBreak, port: by-uf, tolerance: 0.25}
```

and the gate lists them in the process: `statera: {implementation: Gate, checks: [CK-01, CK-02]}`.
`CheckContext` adds `current(port)` and `previous(port)` (the same port in the previous published
edition) and `params` (the control's entry in controls.yaml). The framework's control types become
checks of the framework (`RevisionBetweenEditions`, `Outlier`); the `type: revision-between-editions`
form remains an alias in 0.6.

Each check of a product **must** have cases in `6-analyse/check-cases/<Check>.yaml`: input tables, the
control's parameters and the expected verdict. `statera validate` (group `checks`), and so `statera check`,
runs them like the rule cases (01), without the engine:

```yaml
check: NoRecordsLost                # optional; default: the file stem
cases:
  - id: records-lost
    period: "2026-02"
    params: {port: by-uf, key: [period, uf_abbr], measure: records}   # as in controls.yaml
    current: {by-uf: [{period: "2026-01", uf_abbr: SP, records: 150}]}
    previous: {by-uf: [{period: "2026-01", uf_abbr: SP, records: 192}]}  # omitted port: no previous edition
    expected: failed                # ok | warning | failed: the CheckResult verdict
    message: "192 -> 150"           # optional: must appear in the message
```

The framework's checks need no cases.

### Await

```python
class PartnerFileArrived(Await):
    """The partner's monthly file is in the inbox."""

    def ready(self, ctx: TaskContext) -> bool:
        return (ctx.folder / "inbox" / f"{ctx.period}.csv").exists()
```

While `ready` returns `False`, the edition stays `waiting` on the event and `statera resume`
re-evaluates it. The framework's cut wait becomes the `PinCut` await (09).

### SyntheticSource

```python
class FooSystem(SyntheticSource):
    """The Foo System: one record per request, some received after the cut."""

    sources = ["bronze.foo_records"]  # the bronze contracts it stands in for
    faults = {"distribution"}  # faults it can inject to exercise the checks

    def generate(self, ctx, fault: str | None = None) -> dict[str, int]: ...
```

`SyntheticContext` gives `project`, `product`, `references` (also regenerate the synthetic reference
tables) and `source_folder` (`4-collect/data/source`, where the extraction reads). `statera synthetic`
runs the synthetic sources of every product (or `--product <name>`), in dependency order (the internal
products a product reads come first), and prints what each generated; `statera demo` runs it first.
`--fault` accepts only faults that some source declares, and is given only to the sources that declare it.
It replaces the `[demo] synthetic = "file.py:function"` hook of statera.toml, which remains accepted in 0.6
when no product has a `SyntheticSource` class.

### Binding to the process

The process refers to a class **by name** (`implementation: Deduplicate`, `awaits: PartnerFileArrived`).
The name is resolved in the product first (the files listed in the table above), then in the framework.
Classes are discovered from those fixed files only; a class name is unique in its product.

A product **may** override a framework class: a product class with the same name as a framework class
**must** subclass it, so the declarations and the contract of the step are kept. A product class with
any other name is a new step.

`py:<module>.<function>` and `dbt:build` remain accepted in 0.6, with a deprecation warning for `py:`;
`dbt:build` becomes the framework's `DbtBuild` task.

### Validation

Validation **must** fail, without running any class, when:

- an `implementation` or `awaits` names no class, or a class of the wrong abstraction;
- a product class shadows a framework class without subclassing it;
- the `gsbpm` of the class differs from the task's in the process;
- a gateway after a task tests a condition that is not among the task's `outcomes` (for a gate: `ok`,
  `warning`, `failed`);
- a declared input or output table has no contract, or the task writes to a layer the product does not
  own (09);
- a check listed by a gate has no entry in controls.yaml, or a product check has no cases;
- a `--fault` used in CI is not declared by any synthetic source.
