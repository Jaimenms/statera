"""Extension classes (specification 05): the executor runs Task, Await and Gate classes; validation checks their
declarations without running them; the check cases run like the rule cases."""

import textwrap

import duckdb
import pytest

from statera import execution, validation
from statera.edition import read_jsonl
from statera.extend import cases
from statera.extend import checks as extension_checks

TASKS = """
from statera.extend import Await, Task, TaskResult


class Copy(Task):
    \"\"\"Copies the records, multiplying the value by the `factor` parameter.\"\"\"

    gsbpm = "5.3"
    inputs = ["gold.records"]
    outputs = ["gold.records_copy"]

    def run(self, ctx):
        df = ctx.read("gold.records")
        df["value"] = df["value"] * ctx.params["factor"]
        ctx.write("gold.records_copy", df)
        mode = ctx.params.get("mode")
        if mode == "undeclared":
            ctx.read("bronze.records")
        if mode == "outcome":
            return TaskResult.of("partial")
        if mode == "raise":
            raise ValueError("the source system is down")
        return TaskResult.ok(rows=len(df))


class FileArrived(Await):
    \"\"\"The partner's file is in the edition's folder.\"\"\"

    def ready(self, ctx):
        return (ctx.folder / "inbox.csv").exists()
"""

CHECKS = """
from statera.extend import Check, CheckResult


class Threshold(Check):
    \"\"\"The verdict the control asks for (`verdict`), or: the sum of `value` in port `port` up to `limit`.\"\"\"

    severity = "warning"

    def evaluate(self, ctx):
        if "verdict" in ctx.params:
            verdict = ctx.params["verdict"]
            return {"ok": CheckResult.ok("fine"), "warning": CheckResult.warn("look at it"),
                    "failed": CheckResult.fail("too far")}[verdict]
        current, previous = ctx.current(ctx.params["port"]), ctx.previous(ctx.params["port"])
        total = int(current["value"].sum())
        if previous is not None and total < int(previous["value"].sum()):
            return CheckResult.fail(f"{ctx.period}: total fell to {total}")
        if total > ctx.params["limit"]:
            return CheckResult.warn(f"{ctx.period}: total {total} above {ctx.params['limit']}")
        return CheckResult.ok(f"total {total}")
"""

CASES = """
check: Threshold
cases:
  - id: within-the-limit
    period: "2026-01"
    params: {port: totals, limit: 100}
    current: {totals: [{value: 10}, {value: 20}]}
    expected: ok
  - id: above-the-limit
    period: "2026-01"
    params: {port: totals, limit: 10}
    current: {totals: [{value: 10}, {value: 20}]}
    expected: warning
    message: "above 10"
  - id: total-falls
    period: "2026-02"
    params: {port: totals, limit: 100}
    current: {totals: [{value: 1}]}
    previous: {totals: [{value: 5}]}
    expected: failed
"""

CONTRACT = """
apiVersion: v3.1.0
kind: DataContract
id: {id}
name: {table}
version: 1.0.0
status: active
customProperties:
  - {{property: layer, value: {layer}}}
schema:
  - name: {name}
    physicalName: {table}
    properties:
      - {{name: id, logicalType: integer}}
"""


def process(*elements: str, flows: list[str]) -> str:
    lines = [
        "id: Ext_Process",
        "name: Extension classes",
        "lanes:",
        "  - {id: L1, name: Pipeline, role: team-x}",
        "elements:",
        "  - {id: Start, type: startEvent, name: Start, lane: L1}",
        "  - {id: T_X, type: userTask, name: Investigate, lane: L1, statera: {type: manual, gsbpm: '5.3', "
        "durationMin: 60, exception: true}}",
        "  - {id: End, type: endEvent, name: End, lane: L1}",
        *(f"  - {e}" for e in elements),
        "flows:",
        *(f"  - {f}" for f in flows),
    ]
    return "\n".join(lines) + "\n"


def task(tid: str, implementation: str, gsbpm: str = "5.3", extra: str = "", kind: str = "automatic") -> str:
    return (
        f"{{id: {tid}, type: serviceTask, name: {tid}, lane: L1, statera: {{type: {kind}, gsbpm: '{gsbpm}', "
        f"durationMin: 1, implementation: {implementation}{', ' + extra if extra else ''}}}}}"
    )


def write(project, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = project.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")


def contract(cid: str, table: str, layer: str) -> str:
    return CONTRACT.format(id=cid, table=table, layer=layer, name=table.split(".")[-1])


SAMPLE = "products/sample"


def copy_process(params: str) -> str:
    return process(
        task("T_Copy", "Copy", extra=f"params: {params}"),
        "{id: G, type: exclusiveGateway, name: 'OK?', lane: L1}",
        flows=[
            "{from: Start, to: T_Copy}",
            "{from: T_Copy, to: G}",
            "{from: G, to: End, condition: ok}",
            "{from: G, to: T_X, condition: failed}",
        ],
    )


@pytest.fixture
def warehouse(minimal_project):
    minimal_project.warehouse.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(minimal_project.warehouse))
    con.execute("create schema gold; create schema bronze")
    con.execute("create table gold.records as select * from (values (1, 10), (2, 20)) t(id, value)")
    con.execute("create table bronze.records as select 1 as id")
    con.close()
    write(minimal_project, {f"{SAMPLE}/5-process/tasks.py": TASKS})
    return minimal_project


def run_copy(project, params: str):
    write(project, {f"{SAMPLE}/2-design/process.yaml": copy_process(params)})
    return execution.run("sample", "2026-01")


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


def test_task_class_reads_and_writes_declared_tables_with_params(warehouse):
    ed = run_copy(warehouse, "{factor: 2}")
    assert ed.status == "done", ed.failures
    con = duckdb.connect(str(warehouse.warehouse), read_only=True)
    assert con.execute("select value from gold.records_copy order by id").fetchall() == [(20,), (40,)]
    con.close()
    runs = read_jsonl(warehouse.product("sample").runs_folder / "runs.jsonl")
    assert next(r for r in runs if r["bpmn_task"] == "T_Copy")["metrics"] == {"rows": 2}


def test_lineage_of_the_task_comes_from_its_declarations(warehouse):
    run_copy(warehouse, "{factor: 2}")
    events = read_jsonl(warehouse.product("sample").runs_folder / "events.jsonl")
    task_events = [ev for ev in events if ev["job"]["name"] == "sample.T_Copy"]
    assert task_events
    for ev in task_events:
        assert [d["name"] for d in ev["inputs"]] == ["gold.records"]
        assert [d["name"] for d in ev["outputs"]] == ["gold.records_copy"]


def test_undeclared_table_fails_the_task(warehouse):
    ed = run_copy(warehouse, "{factor: 2, mode: undeclared}")
    assert (ed.status, ed.pending) == ("blocked", "T_X")
    assert any("UndeclaredTable" in f and "bronze.records" in f for f in ed.failures)


def test_outcome_outside_outcomes_fails(warehouse):
    ed = run_copy(warehouse, "{factor: 2, mode: outcome}")
    assert (ed.status, ed.pending) == ("blocked", "T_X")
    assert any("outcome 'partial' is not among" in f for f in ed.failures)


def test_exception_fails_with_its_message(warehouse):
    ed = run_copy(warehouse, "{factor: 2, mode: raise}")
    assert (ed.status, ed.pending) == ("blocked", "T_X")
    assert any("ValueError: the source system is down" in f for f in ed.failures)


def test_await_class_waits_then_resumes(minimal_project):
    write(
        minimal_project,
        {
            f"{SAMPLE}/4-collect/tasks.py": TASKS,
            f"{SAMPLE}/2-design/process.yaml": process(
                "{id: Ev, type: intermediateCatchEvent, name: File, lane: L1, event: message, "
                "statera: {awaits: FileArrived}}",
                flows=["{from: Start, to: Ev}", "{from: Ev, to: End}"],
            ),
        },
    )
    ed = execution.run("sample", "2026-01")
    assert (ed.status, ed.pending) == ("waiting", "Ev")
    folder = minimal_project.product("sample").data(5) / "2026-01"  # no gsbpm on the event: phase 5
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "inbox.csv").write_text("id\n1\n")
    ed = execution.resume("sample", "2026-01")
    assert ed.status == "done"
    assert [h["task"] for h in ed.history] == ["Ev"]


def gate_project(project, controls: str, warning_flow: bool, gate_class: str = "Gate", tasks: str | None = None):
    flows = [
        "{from: Start, to: T_Gate}",
        "{from: T_Gate, to: G}",
        "{from: G, to: End, condition: ok}",
        "{from: G, to: T_X, condition: failed}",
    ]
    elements = [
        task("T_Gate", gate_class, gsbpm="6.2", extra="checks: [CK-01]"),
        "{id: G, type: exclusiveGateway, name: 'OK?', lane: L1}",
    ]
    if warning_flow:
        elements.append(
            "{id: T_W, type: userTask, name: Review, lane: L1, statera: {type: manual, gsbpm: '6.5', "
            "durationMin: 30, exception: true}}"
        )
        flows.append("{from: G, to: T_W, condition: warning}")
    files = {
        f"{SAMPLE}/2-design/process.yaml": process(*elements, flows=flows),
        f"{SAMPLE}/6-analyse/checks.py": CHECKS,
        f"{SAMPLE}/6-analyse/controls.yaml": f"controls:\n  - {controls}\n",
    }
    if tasks:
        files[f"{SAMPLE}/6-analyse/tasks.py"] = tasks
    write(project, files)


@pytest.mark.parametrize(
    ("controls", "warning_flow", "status", "pending"),
    [
        ("{id: CK-01, check: Threshold, verdict: warning}", True, "blocked", "T_W"),
        ("{id: CK-01, check: Threshold, verdict: warning}", False, "done", None),
        ("{id: CK-01, check: Threshold, verdict: failed}", True, "blocked", "T_W"),  # severity warning: warns
        ("{id: CK-01, check: Threshold, verdict: failed, severity: error}", True, "blocked", "T_X"),
        ("{id: CK-01, check: Threshold, verdict: ok}", True, "done", None),
    ],
)
def test_gate_with_a_product_check(warehouse, controls, warning_flow, status, pending):
    gate_project(warehouse, controls, warning_flow)
    ed = execution.run("sample", "2026-01")
    assert (ed.status, ed.pending) == (status, pending)
    if pending == "T_X":  # the check of severity error failed (not something else)
        assert any("CK-01" in f and "too far" in f for f in ed.failures), ed.failures


OVERRIDE = """
import statera.extend

CALLS = []


class Gate(statera.extend.Gate):
    def run(self, ctx):
        CALLS.append(ctx.period)
        return super().run(ctx)
"""


def test_product_class_overrides_a_framework_class(warehouse):
    from statera.extend import registry

    gate_project(warehouse, "{id: CK-01, check: Threshold, verdict: ok}", False, tasks=OVERRIDE)
    ed = execution.run("sample", "2026-01")
    assert ed.status == "done"
    cls = registry.resolve(warehouse.product("sample"), "Gate", registry.Task)
    assert cls.__module__ != "statera.extend.framework"
    assert __import__(cls.__module__).CALLS == ["2026-01"]


def test_py_implementation_still_runs_with_a_deprecation_warning(minimal_project):
    with pytest.warns(DeprecationWarning, match="py:testing_steps.record"):
        ed = execution.run("sample", "2026-01", approve=True)
    assert ed.status == "done"


# ---------------------------------------------------------------------------
# Static validation
# ---------------------------------------------------------------------------


def failures(project) -> list[str]:
    return [message for ok, message in extension_checks.findings(project) if not ok]


def clean_project(project):
    """A consistent product: a Task with contracts in the layer it owns, a gate with a cased check."""
    write(
        project,
        {
            f"{SAMPLE}/5-process/tasks.py": TASKS,
            f"{SAMPLE}/5-process/contracts/records.odcs.yaml": contract("gold-records", "gold.records", "gold"),
            f"{SAMPLE}/5-process/contracts/copy.odcs.yaml": contract("gold-copy", "gold.records_copy", "gold"),
            f"{SAMPLE}/6-analyse/check-cases/Threshold.yaml": CASES,
        },
    )
    gate_project(project, "{id: CK-01, check: Threshold, verdict: ok}", True)
    proc = (project.root / SAMPLE / "2-design/process.yaml").read_text()
    proc = proc.replace(
        "  - {from: Start, to: T_Gate}", "  - {from: Start, to: T_Copy}\n  - {from: T_Copy, to: T_Gate}"
    )
    proc = proc.replace("flows:", "  - " + task("T_Copy", "Copy", extra="params: {factor: 2}") + "\nflows:")
    write(project, {f"{SAMPLE}/2-design/process.yaml": proc})


def edit(project, rel: str, old: str, new: str) -> None:
    path = project.root / rel
    text = path.read_text()
    assert old in text, old
    path.write_text(text.replace(old, new))


PROCESS_FILE = f"{SAMPLE}/2-design/process.yaml"


def test_clean_extensions_have_no_failures(minimal_project):
    clean_project(minimal_project)
    assert failures(minimal_project) == []
    oks = [m for ok, m in extension_checks.findings(minimal_project) if ok]
    assert any("sample: extension classes declared consistently" in m for m in oks)


def test_legacy_py_forms_are_informative(minimal_project):
    messages = extension_checks.findings(minimal_project)
    assert all(ok for ok, _ in messages)
    assert any("py:statera.collect.pin_cut" in m and "use PinCut" in m for _, m in messages)
    assert any("py:testing_steps.record" in m and "deprecated" in m for _, m in messages)


def test_rule_implementation_names_no_class(minimal_project):
    clean_project(minimal_project)
    edit(minimal_project, PROCESS_FILE, "implementation: Copy", "implementation: Kopy")
    assert any("`implementation: Kopy` names no class" in m for m in failures(minimal_project))


def test_rule_implementation_of_the_wrong_abstraction(minimal_project):
    clean_project(minimal_project)
    edit(minimal_project, PROCESS_FILE, "implementation: Copy", "implementation: FileArrived")
    assert any("`implementation: FileArrived` is an Await, not a Task" in m for m in failures(minimal_project))


def test_rule_shadowing_without_subclassing(minimal_project):
    clean_project(minimal_project)
    write(
        minimal_project,
        {f"{SAMPLE}/7-disseminate/tasks.py": "from statera.extend import Task\n\n\nclass Publish(Task):\n    pass\n"},
    )
    assert any(
        "class Publish has the name of a framework class but does not subclass it" in m
        for m in failures(minimal_project)
    )


def test_rule_gsbpm_differs(minimal_project):
    clean_project(minimal_project)
    edit(minimal_project, PROCESS_FILE, "id: T_Copy, type: serviceTask, name: T_Copy, lane: L1, statera: {type: "
         "automatic, gsbpm: '5.3'", "id: T_Copy, type: serviceTask, name: T_Copy, lane: L1, statera: {type: "
         "automatic, gsbpm: '7.3'")  # fmt: skip
    assert any("Copy is GSBPM 5.3, the task in the process is 7.3" in m for m in failures(minimal_project))


def test_rule_condition_outside_outcomes(minimal_project):
    clean_project(minimal_project)
    edit(minimal_project, PROCESS_FILE, "{from: T_Copy, to: T_Gate}",
         "{from: T_Copy, to: G0}\n  - {from: G0, to: T_Gate, condition: ok}\n  - {from: G0, to: T_X, condition: partial}")  # fmt: skip
    edit(
        minimal_project,
        PROCESS_FILE,
        "flows:",
        "  - {id: G0, type: exclusiveGateway, name: 'Copied?', lane: L1}\nflows:",
    )
    assert any(
        "gateway G0 after T_Copy tests 'partial', which is not among Copy.outcomes ['failed', 'ok']" in m
        for m in failures(minimal_project)
    )


def test_rule_table_without_contract(minimal_project):
    clean_project(minimal_project)
    (minimal_project.root / SAMPLE / "5-process/contracts/records.odcs.yaml").unlink()
    assert any("Copy reads gold.records, which has no contract" in m for m in failures(minimal_project))


def test_rule_output_of_another_product(minimal_project):
    clean_project(minimal_project)
    edit(minimal_project, f"{SAMPLE}/5-process/tasks.py", 'outputs = ["gold.records_copy"]',
         'outputs = ["gold.records_copy", "bronze.records"]')  # fmt: skip
    assert any(
        "Copy writes bronze.records, whose contract belongs to base-data" in m for m in failures(minimal_project)
    )


def test_rule_output_in_a_layer_the_product_does_not_own(minimal_project):
    clean_project(minimal_project)
    write(
        minimal_project, {f"{SAMPLE}/5-process/contracts/copy.odcs.yaml": contract("x", "gold.records_copy", "silver")}
    )
    assert any(
        "Copy writes gold.records_copy (layer silver): a consumer product writes the gold layer" in m
        for m in failures(minimal_project)
    )


def test_rule_gate_lists_a_check_without_control(minimal_project):
    clean_project(minimal_project)
    edit(minimal_project, PROCESS_FILE, "checks: [CK-01]", "checks: [CK-01, CK-09]")
    assert any("T_Gate lists check CK-09, which has no entry in" in m for m in failures(minimal_project))


def test_rule_control_does_not_resolve_to_a_check(minimal_project):
    clean_project(minimal_project)
    edit(minimal_project, f"{SAMPLE}/6-analyse/controls.yaml", "check: Threshold", "check: Copy")
    assert any("CK-01 `check: Copy` does not resolve to a Check" in m for m in failures(minimal_project))


def test_rule_product_check_without_cases(minimal_project):
    clean_project(minimal_project)
    (minimal_project.root / SAMPLE / "6-analyse/check-cases/Threshold.yaml").unlink()
    assert any("sample: check Threshold has no cases" in m for m in failures(minimal_project))


SYNTHETIC = """
from statera.extend import SyntheticSource


class BaseSystem(SyntheticSource):
    sources = ["bronze.records"]
    faults = {"distribution"}

    def generate(self, ctx, fault=None):
        return {}
"""


def test_rule_fault_used_in_ci_is_declared(minimal_project):
    clean_project(minimal_project)
    (minimal_project.root / ".git").mkdir()  # the repository root: where .github/workflows lives
    workflow = "jobs:\n  demo:\n    steps:\n      - run: statera synthetic --fault distribution\n"
    write(
        minimal_project,
        {"products/base-data/3-build/synthetic.py": SYNTHETIC, ".github/workflows/ci.yml": workflow},
    )
    assert failures(minimal_project) == []
    write(minimal_project, {".github/workflows/demo.yml": "steps:\n  - run: statera synthetic --fault=missing\n"})
    assert any(
        ".github/workflows/demo.yml: `--fault missing` is not declared by any synthetic source" in m
        for m in failures(minimal_project)
    )


def test_validation_puts_the_findings_in_the_processes_group(minimal_project):
    clean_project(minimal_project)
    edit(minimal_project, PROCESS_FILE, "implementation: Copy", "implementation: Kopy")
    manifest = {"nodes": {}, "sources": {}}
    found = [f for f in validation.check_processes(minimal_project, manifest) if "Kopy" in f.message]
    assert found and all(f.group == "processes" and not f.ok for f in found)


# ---------------------------------------------------------------------------
# Check cases
# ---------------------------------------------------------------------------


def test_check_cases_pass(minimal_project):
    clean_project(minimal_project)
    results = cases.run_check_cases(minimal_project)
    assert [(r.case, r.status) for r in results] == [
        ("within-the-limit", "ok"),
        ("above-the-limit", "ok"),
        ("total-falls", "ok"),
    ]
    findings = validation.check_check_cases(minimal_project)
    assert [(f.group, f.ok) for f in findings] == [("checks", True)]
    assert "3 case(s) of 1 check(s) passed" in findings[0].message


def test_check_cases_fail(minimal_project):
    clean_project(minimal_project)
    edit(minimal_project, f"{SAMPLE}/6-analyse/check-cases/Threshold.yaml", 'message: "above 10"', 'message: "below"')
    edit(minimal_project, f"{SAMPLE}/6-analyse/check-cases/Threshold.yaml", "expected: failed", "expected: ok")
    edit(minimal_project, f"{SAMPLE}/6-analyse/check-cases/Threshold.yaml", "current: {totals: [{value: 1}]}",
         "current: {other: [{value: 1}]}")  # fmt: skip
    results = {r.case: r for r in cases.run_check_cases(minimal_project)}
    assert results["within-the-limit"].status == "ok"
    assert results["above-the-limit"].status == "failed"
    assert "does not contain 'below'" in results["above-the-limit"].message
    assert results["total-falls"].status == "failed"
    assert "KeyError" in results["total-falls"].message  # an exception in evaluate fails the case
    findings = validation.check_check_cases(minimal_project)
    assert [f.ok for f in findings] == [False, False]
    assert findings[0].message.startswith("sample: Threshold / above-the-limit:")


def test_check_case_with_a_wrong_verdict(minimal_project):
    clean_project(minimal_project)
    edit(minimal_project, f"{SAMPLE}/6-analyse/check-cases/Threshold.yaml", "expected: warning", "expected: ok")
    results = {r.case: r for r in cases.run_check_cases(minimal_project)}
    assert results["above-the-limit"].status == "failed"
    assert "verdict 'warning', expected 'ok'" in results["above-the-limit"].message
