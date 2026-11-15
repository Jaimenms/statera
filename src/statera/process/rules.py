"""Business rules with executable cases.

Each rule lives in `<unit>/5-process/rules/<ID>-<slug>/` (in the aggregate internal product, if the
rule applies to all consumers; in the dissemination product, if it defines that product's scope), with:
  - rule.md   : definition, normative source, implementation
  - cases.yaml : examples of input and expected output (schema: statera rule-cases.schema.json)

The cases are the single source. For SQL models, this module converts them into dbt *unit tests*, in a
GENERATED file at `dbt/generated/_rule_unit_tests.yml`. For Python models (dbt only does
unit tests for SQL), it runs the cases by calling the model's pure function, `transform(inputs,
variables)`, with the same inputs and expected outputs. The rule described for people and the test
run by the machine are the same thing, in both languages.
"""

from __future__ import annotations

import importlib.util
import math
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from statera import generated
from statera.project import Project, Unit, current, read_yaml

HEADER = generated.header(
    "statera rules sync",
    "<unit>/5-process/rules/*/cases.yaml (one unit test per case of each rule)",
)


@dataclass
class Rule:
    id: str
    folder: Path
    title: str
    model: str
    cases: list[dict]
    variables: dict
    unit: Unit | None = None
    requires: list[str] = field(default_factory=list)

    @property
    def slug(self) -> str:
        return self.folder.name


def unit_tests_file(project: Project | None = None) -> Path:
    return (project or current()).dbt_folder / "generated" / "_rule_unit_tests.yml"


def load(project: Project | None = None) -> list[Rule]:
    project = project or current()
    rules = []
    for base in project.rule_folders():
        for folder in sorted(p for p in base.iterdir() if p.is_dir()):
            cases = read_yaml(folder / "cases.yaml")
            md = (folder / "rule.md").read_text(encoding="utf-8")
            rules.append(
                Rule(
                    id=cases["rule"],
                    folder=folder,
                    title=md.splitlines()[0].lstrip("# ").strip(),
                    model=cases["model"],
                    cases=cases["cases"],
                    variables=cases.get("variables", {}),
                    unit=project.unit_of(folder),
                    requires=list(cases.get("requires", [])),
                )
            )
    return sorted(rules, key=lambda r: r.id)


def _test_name(rule: Rule, case: dict) -> str:
    return re.sub(r"[^a-z0-9_]", "_", f"{rule.id}__{case['id']}".lower())


def python_model(project: Project, name: str) -> Path | None:
    """The dbt Python model file with that name, if any (`<unit>/5-process/models/**/<name>.py`)."""
    for u in project.products():
        if findings := sorted((u.phase(5) / "models").rglob(f"{name}.py")):
            return findings[0]
    return None


def generate_unit_tests(project: Project | None = None) -> str:
    project = project or current()
    unit_tests = []
    for rule in load(project):
        if python_model(project, rule.model):
            continue  # dbt does not unit-test Python models: the cases run in run_python_cases
        for case in rule.cases:
            test = {
                "name": _test_name(rule, case),
                "description": f"{rule.id}: {case['description']}",
                "model": rule.model,
                "given": [{"input": f"ref('{input_}')", "rows": lines} for input_, lines in case["inputs"].items()],
                "expect": {"rows": case["expected"]},
                "config": {"meta": {"rule": rule.id, "case": case["id"]}},
            }
            if rule.variables:
                test["overrides"] = {"vars": rule.variables}
            unit_tests.append(test)
    body = yaml.safe_dump({"version": 2, "unit_tests": unit_tests}, allow_unicode=True, sort_keys=False, width=110)
    return HEADER + body


def sync(project: Project | None = None) -> Path:
    destination = unit_tests_file(project)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(generate_unit_tests(project), encoding="utf-8")
    return destination


def is_synced(project: Project | None = None) -> bool:
    destination = unit_tests_file(project)
    return destination.exists() and destination.read_text(encoding="utf-8") == generate_unit_tests(project)


# ---------------------------------------------------------------------------
# Cases of rules implemented in Python models
# ---------------------------------------------------------------------------


@dataclass
class CaseResult:
    rule: str
    case: str
    status: str  # "ok", "failed" or "not-run"
    message: str = ""


def _transform_function(file: Path):
    name = f"statera_model_{file.stem}"
    spec = importlib.util.spec_from_file_location(name, file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    if not hasattr(module, "transform"):
        raise AttributeError(f"{file.name} does not define transform(inputs, variables)")
    return module.transform


def _normalize(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float):
        return round(value, 6) if not value.is_integer() else int(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()[:10] if len(value.isoformat()) >= 10 else value.isoformat()
    return value


def _compare(actual, expected: list[dict]) -> str:
    """Same as the dbt unit test: the same rows, in the expected columns, regardless of order."""
    columns = list(dict.fromkeys(c for line in expected for c in line))
    missing_columns = [c for c in columns if c not in actual.columns]
    if missing_columns:
        return f"columns missing from the output: {missing_columns}"
    actual_rows = sorted(
        (tuple(_normalize(v) for v in line) for line in actual[columns].itertuples(index=False)), key=repr
    )
    expected_rows = sorted((tuple(_normalize(line.get(c)) for c in columns) for line in expected), key=repr)
    if actual_rows != expected_rows:
        return f"expected {expected_rows}, got {actual_rows} (columns {columns})"
    return ""


def run_python_cases(project: Project | None = None) -> list[CaseResult]:
    """Runs the cases of the rules whose model is Python. Cases that require a missing tool
    (pspp, r) are "not-run", except with STATERA_REQUIRE=<tools> (CI), where they fail."""
    import pandas as pd

    from statera.legacy import pspp, r

    project = project or current()
    available = {"pspp": pspp.available(), "r": r.available()}
    required = {f.strip() for f in os.environ.get("STATERA_REQUIRE", "").split(",") if f.strip()}
    results = []
    for rule in load(project):
        file = python_model(project, rule.model)
        if file is None:
            continue
        absent = [f for f in rule.requires if not available.get(f)]
        if absent:
            status = "failed" if set(absent) & required else "not-run"
            results += [
                CaseResult(rule.id, c["id"], status, f"requires {', '.join(absent)}, not installed") for c in rule.cases
            ]
            continue
        try:
            transform = _transform_function(file)
        except Exception as error:  # noqa: BLE001 - any load errorr is a case failure
            results += [CaseResult(rule.id, c["id"], "failed", str(error)) for c in rule.cases]
            continue
        for case in rule.cases:
            inputs = {name: pd.DataFrame(lines) for name, lines in case["inputs"].items()}
            try:
                difference = _compare(transform(inputs, dict(rule.variables)), case["expected"])
            except Exception as error:  # noqa: BLE001
                difference = f"{type(error).__name__}: {error}"
            results.append(CaseResult(rule.id, case["id"], "failed" if difference else "ok", difference))
    return results
