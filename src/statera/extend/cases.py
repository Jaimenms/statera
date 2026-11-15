"""Cases of the products' checks (specification 05): `6-analyse/check-cases/<Check>.yaml`, run by validation
(group "checks") and `statera check`, like the rule cases (01).

    check: <CheckClass>              # optional; default: the file stem
    cases:
      - id: <slug>
        period: "2026-02"
        params: {port: by-uf, tolerance: 0.25}      # the control's parameters, as in controls.yaml
        current: {<port>: [{col: value, ...}, ...]}  # rows of each port in this edition
        previous: {<port>: [...]}                    # optional; a port left out has no previous edition
        expected: ok | warning | failed              # the CheckResult.verdict
        message: "<substring>"                       # optional: must appear in CheckResult.message
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from statera.extend import registry
from statera.extend.base import VERDICTS, Check, CheckContext, CheckResult


@dataclass
class CaseResult:
    product: str
    check: str
    case: str
    status: str  # "ok" | "failed"
    message: str = ""


def case_files(unit) -> list[Path]:
    folder = unit.phase(6) / "check-cases"
    return sorted(folder.glob("*.yaml")) if folder.is_dir() else []


def _tables(rows_by_port: dict | None, required: bool):
    import pandas as pd

    rows_by_port = rows_by_port or {}

    def table(port: str):
        if port not in rows_by_port:
            if required:
                raise KeyError(f"the case gives no rows for port '{port}' (current)")
            return None
        return pd.DataFrame(rows_by_port[port] or [])

    return table


def run_case(unit, cls: type, case: dict, period_column: str) -> tuple[bool, str]:
    """(passed, why not) of one case: builds the CheckContext from the case and compares the verdict."""
    expected = case.get("expected")
    if expected not in VERDICTS:
        return False, f"`expected` must be one of {', '.join(VERDICTS)} (got {expected!r})"
    context = CheckContext(
        unit.name,
        str(case.get("period", "")),
        case.get("params") or {},
        current=_tables(case.get("current"), required=True),
        previous=_tables(case.get("previous"), required=False),
        period_column=period_column,
        log=lambda message: None,
    )
    try:
        result = cls().evaluate(context)
    except Exception as error:  # noqa: BLE001 - an exception in evaluate is a failed case
        return False, f"{type(error).__name__}: {error}"
    if not isinstance(result, CheckResult):
        return False, f"evaluate returned {type(result).__name__}, not a CheckResult"
    if result.verdict != expected:
        return False, f"verdict '{result.verdict}', expected '{expected}': {result.message}"
    substring = case.get("message")
    if substring and str(substring) not in (result.message or ""):
        return False, f"message {result.message!r} does not contain {substring!r}"
    return True, ""


def run_check_cases(project) -> list[CaseResult]:
    """Runs the cases of every product's checks (framework checks may have cases too; they need none)."""
    period_column = project.conventions["period_column"]
    results = []
    for unit in project.products():
        for file in case_files(unit):
            rel = project.rel(file)
            try:
                data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as error:
                results.append(CaseResult(unit.name, file.stem, "-", "failed", f"{rel}: invalid YAML: {error}"))
                continue
            if not isinstance(data, dict) or not isinstance(data.get("cases"), list) or not data["cases"]:
                results.append(CaseResult(unit.name, file.stem, "-", "failed", f"{rel}: no `cases` list"))
                continue
            name = data.get("check") or file.stem
            try:
                cls = registry.resolve(unit, name, Check)
            except registry.ExtensionError as error:
                results.append(CaseResult(unit.name, name, "-", "failed", f"{rel}: {error}"))
                continue
            for number, case in enumerate(data["cases"], 1):
                case = case if isinstance(case, dict) else {}
                passed, message = run_case(unit, cls, case, period_column)
                case_id = str(case.get("id") or f"case {number}")
                results.append(CaseResult(unit.name, name, case_id, "ok" if passed else "failed", message))
    return results
