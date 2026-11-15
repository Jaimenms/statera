"""Static validation of the extension classes (specification 05, "Extension classes" > "Validation").

Checks the classes' declarations against the processes, the contracts, the controls and CI, WITHOUT running
any class: the product files are imported (that defines the classes), and the only method called is the
`tables(unit)` declaration — never `run`, `ready`, `evaluate` or `generate`. It fails when:

  1. an `implementation` (Task) or `awaits` (Await) names no class, or a class of the wrong abstraction
     (the `py:` forms of 0.5 are still accepted: their uses are listed as informative findings);
  2. a product class shadows a framework class without subclassing it (or is defined twice, or its file does
     not import);
  3. the `gsbpm` of the class does not overlap the task's in the process;
  4. a gateway right after an automatic task tests a `condition` that is not among the class's `outcomes`;
  5. a declared input or output table has no contract, or an output's contract is not the product's own or
     is in a layer the product does not own (09: source -> bronze, aggregate -> silver, consumer -> gold);
  6. a check listed by a gate has no entry in 6-analyse/controls.yaml, a control does not resolve to a Check,
     or a product Check has no cases (6-analyse/check-cases/<Check>.yaml);
  7. a `--fault` used in CI (.github/workflows of the repository root) is not declared by any synthetic source.

`findings(project)` returns (ok, message) pairs; validation puts them in the "processes" group.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from statera import contracts
from statera.design import bpmn, processes
from statera.extend import framework, registry
from statera.extend.base import Await, Check, SyntheticSource, Task, gsbpm_codes

Finding = tuple[bool, str]

FAULT = re.compile(r"--fault(?:=|\s+)(['\"]?)([\w.-]+)\1")
HUMAN_TYPES = ("manual", "semi-automatic")
KIND_FILES = {Task: "<phase>/tasks.py", Await: "<phase>/tasks.py"}


def owned_layer(alignment: str) -> str | None:
    """The layer a product of this alignment writes (09): source -> bronze, aggregate -> silver, consumer -> gold."""
    from statera.validation import EXPECTED_LAYER

    return next((layer for (a, _), layer in EXPECTED_LAYER.items() if a == alignment), None)


def _contracts_by_table(project) -> dict[str, tuple[Path, dict]]:
    by_table = {}
    for path, contract in contracts.all_(project).values():
        try:
            by_table[contracts.physical_table(contract)] = (path, contract)
        except (KeyError, IndexError, TypeError):  # malformed contract: reported by the "schemas" group
            continue
    return by_table


# ---------------------------------------------------------------------------
# Rule 1: binding
# ---------------------------------------------------------------------------


def _reference(e: dict) -> tuple[str, str, type] | None:
    """(key, reference, abstraction) of a process element that names a class, or None."""
    ext = e.get("statera") or {}
    if e["type"] in bpmn.TASKS and ext.get("implementation"):
        return "implementation", ext["implementation"], Task
    if e["type"] not in bpmn.TASKS and ext.get("awaits"):
        return "awaits", ext["awaits"], Await
    return None


def _not_bound(where: str, key: str, reference: str, kind: type, classes: dict[str, type] | None) -> str:
    """Why a reference does not bind. `classes`: the product's (None in a cross-cutting process)."""
    name = framework.LEGACY.get(reference, reference)
    cls = (classes or {}).get(name) or framework.FRAMEWORK.get(name)
    if cls is None:
        found_in = "the framework" if classes is None else f"the product's {KIND_FILES[kind]} nor the framework"
        return f"{where} `{key}: {reference}` names no class (none in {found_in})"
    actual = next((registry.KIND_NAME[k] for k in registry.KIND_NAME if issubclass(cls, k)), "class")
    expected = registry.KIND_NAME[kind]
    return f"{where} `{key}: {reference}` is {_article(actual)} {actual}, not {_article(expected)} {expected}"


def _article(noun: str) -> str:
    return "an" if noun[:1] in "AEIOU" else "a"


def _bind(unit, where: str, key: str, reference: str, kind: type) -> tuple[type | None, list[Finding]]:
    """The class an element names (None for a legacy function or when it does not bind), with the findings."""
    if reference.startswith("dbt:") and reference not in framework.LEGACY:
        return None, [(False, f"{where} `{key}: {reference}`: only dbt:build is accepted; use DbtBuild with `models`")]
    if reference.startswith("py:") and reference not in framework.LEGACY:
        try:
            registry.bind(unit, reference, kind)
        except Exception:  # noqa: BLE001 - a `py:` reference that does not import: reported by bpmn.validate
            return None, []
        replacement = f"{_article(registry.KIND_NAME[kind])} {registry.KIND_NAME[kind]} class"
        return None, [(True, f"{where} `{key}: {reference}` is deprecated since statera 0.6: use {replacement}")]
    try:
        binding = registry.bind(unit, reference, kind)
    except registry.ExtensionError:
        return None, [(False, _not_bound(where, key, reference, kind, registry.product_classes(unit).classes))]
    notes = []
    if binding.deprecated:
        notes.append((True, f"{where} `{key}: {reference}` is deprecated since statera 0.6: use {binding.deprecated}"))
    return binding.cls, notes


def _bind_framework(where: str, key: str, reference: str, kind: type) -> list[Finding]:
    """A cross-cutting process (governance/processes) has no product: only the framework's classes."""
    if reference.startswith("py:") and reference not in framework.LEGACY:
        return [(True, f"{where} `{key}: {reference}` is deprecated since statera 0.6: use a framework class")]
    name = framework.LEGACY.get(reference, reference)
    cls = framework.FRAMEWORK.get(name)
    if cls is None or not issubclass(cls, kind):
        return [(False, _not_bound(where, key, reference, kind, None))]
    if reference.startswith("py:"):
        return [(True, f"{where} `{key}: {reference}` is deprecated since statera 0.6: use {name}")]
    return []


# ---------------------------------------------------------------------------
# Rules 3 to 6 on a product's process
# ---------------------------------------------------------------------------


def _gsbpm(where: str, cls: type, ext: dict) -> list[Finding]:
    try:
        declared, modeled = gsbpm_codes(cls.gsbpm), gsbpm_codes(ext.get("gsbpm"))
    except ValueError:
        return [
            (False, f"{where} {cls.__name__}.gsbpm '{cls.gsbpm}' is not a GSBPM sub-process (e.g. '5.3', '5.1-5.8')")
        ]
    if declared and modeled and not declared & modeled:
        return [(False, f"{where} {cls.__name__} is GSBPM {cls.gsbpm}, the task in the process is {ext['gsbpm']}")]
    return []


def _conditions(rel: str, e: dict, cls: type, elements: dict, outgoing: dict) -> list[Finding]:
    """Rule 4: the gateway right after an automatic task tests only the class's outcomes."""
    ext = e.get("statera") or {}
    if ext.get("type") in HUMAN_TYPES:  # the gateway after a human task tests the decision
        return []
    out = []
    for flow in outgoing.get(e["id"], []):
        gateway = elements.get(flow["to"])
        if not gateway or gateway["type"] not in bpmn.GATEWAYS:
            continue
        for f in outgoing.get(gateway["id"], []):
            condition = f.get("condition")
            if condition and condition not in cls.outcomes:
                out.append(
                    (
                        False,
                        f"{rel}: gateway {gateway['id']} after {e['id']} tests '{condition}', which is "
                        f"not among {cls.__name__}.outcomes {sorted(cls.outcomes)}",
                    )
                )
    return out


def _tables(project, unit, cls: type, by_table: dict) -> list[Finding]:
    """Rule 5: declared tables have contracts; outputs are the product's own, in the layer it owns."""
    try:
        inputs, outputs = cls.tables(unit)
    except Exception as error:  # noqa: BLE001 - a declaration that cannot be read
        return [(False, f"{unit.name}: {cls.__name__}.tables() does not give the declared tables: {error}")]
    out = []
    for table in dict.fromkeys([*inputs, *outputs]):
        if table not in by_table:
            role = "writes" if table in outputs else "reads"
            out.append(
                (
                    False,
                    f"{unit.name}: {cls.__name__} {role} {table}, which has no contract "
                    f"(no ODCS contract with that physicalName)",
                )
            )
    expected = owned_layer(unit.alignment)
    for table in outputs:
        if table not in by_table:
            continue
        path, contract = by_table[table]
        owner = project.unit_of(path)
        if owner != unit:
            owner_name = owner.name if owner else project.rel(path)
            out.append(
                (
                    False,
                    f"{unit.name}: {cls.__name__} writes {table}, whose contract belongs to "
                    f"{owner_name} ({project.rel(path)}): a product writes only its own tables",
                )
            )
        elif expected and contracts.layer(contract) != expected:
            out.append(
                (
                    False,
                    f"{unit.name}: {cls.__name__} writes {table} (layer {contracts.layer(contract)}): "
                    f"a {unit.alignment} product writes the {expected} layer (specification 09)",
                )
            )
    return out


def _controls(unit) -> list[dict]:
    return (unit.read("6-analyse/controls.yaml") or {}).get("controls") or []


def _checks(project, unit, classes: dict[str, type]) -> list[Finding]:
    """Rule 6 (controls and cases): every control resolves to a Check; every product Check has cases."""
    out = []
    rel = project.rel(unit.phase(6) / "controls.yaml")
    for control in _controls(unit):
        try:
            registry.check_class(unit, control)
        except registry.ExtensionError:
            named = control.get("check") or control.get("type")
            text = f"`check: {named}`" if named else "neither `check` nor a known `type`"
            out.append(
                (
                    False,
                    f"{rel}: {control.get('id')} {text} does not resolve to a Check (the product's "
                    f"6-analyse/checks.py or the framework's)",
                )
            )
    folder = unit.phase(6) / "check-cases"
    covered = set()
    for file in sorted(folder.glob("*.yaml")) if folder.is_dir() else []:
        try:
            data = yaml.safe_load(file.read_text(encoding="utf-8"))
        except yaml.YAMLError:  # the "checks" group reports it
            data = None
        covered.add((data.get("check") if isinstance(data, dict) else None) or file.stem)
    for name, cls in sorted(classes.items()):
        if issubclass(cls, Check) and name not in covered:
            out.append(
                (
                    False,
                    f"{unit.name}: check {name} has no cases: add "
                    f"{project.rel(folder / f'{name}.yaml')} (input tables, parameters, expected verdict)",
                )
            )
    return out


def _process(project, unit) -> tuple[list[Finding], set[type]]:
    """Rules 1, 3, 4, 5 and 6 (gates) on the product's process. Returns the findings and the Task classes bound."""
    if not unit.processes():
        return [], set()
    rel = project.rel(unit.process)
    try:
        proc = processes.load(unit.process)
        if processes.validate_structure(proc):
            return [], set()  # reported by the "processes" group
    except Exception:  # noqa: BLE001 - invalid YAML: reported by the "schemas" group
        return [], set()
    elements = {e["id"]: e for e in proc["elements"]}
    outgoing: dict[str, list[dict]] = {}
    for f in proc["flows"]:
        outgoing.setdefault(f["from"], []).append(f)
    control_ids = {c.get("id") for c in _controls(unit)}
    out, bound = [], set()
    for e in proc["elements"]:
        ext = e.get("statera") or {}
        where = f"{rel}: {e['id']}"
        for check_id in ext.get("checks") or []:
            if check_id not in control_ids:
                out.append(
                    (
                        False,
                        f"{where} lists check {check_id}, which has no entry in "
                        f"{project.rel(unit.phase(6) / 'controls.yaml')}",
                    )
                )
        named = _reference(e)
        if named is None:
            continue
        key, reference, kind = named
        cls, notes = _bind(unit, where, key, reference, kind)
        out += notes
        if cls is None or kind is not Task:
            continue
        bound.add(cls)
        out += _gsbpm(where, cls, ext)
        out += _conditions(rel, e, cls, elements, outgoing)
    return out, bound


def _cross_cutting(project) -> list[Finding]:
    out = []
    for path in sorted((project.governance / "processes").glob("*.yaml")):
        try:
            proc = processes.load(path) or {}
            elements = proc.get("elements") or []
        except Exception:  # noqa: BLE001 - invalid YAML: reported by the "schemas" group
            continue
        for e in elements:
            named = _reference(e) if isinstance(e, dict) and "type" in e else None
            if named:
                out += _bind_framework(f"{project.rel(path)}: {e.get('id')}", *named)
    return out


# ---------------------------------------------------------------------------
# Rule 7: faults used in CI
# ---------------------------------------------------------------------------


def _faults(project, synthetic: list[type]) -> list[Finding]:
    from statera import governance

    if not synthetic:
        return []
    declared = {fault for cls in synthetic for fault in cls.faults}
    root = governance.git_root(project)
    out, used = [], 0
    for workflow in sorted((root / ".github" / "workflows").glob("*.y*ml")):
        name = workflow.relative_to(root).as_posix()
        for match in FAULT.finditer(workflow.read_text(encoding="utf-8", errors="ignore")):
            used += 1
            fault = match.group(2)
            if fault not in declared:
                known = ", ".join(sorted(declared)) or "none"
                out.append(
                    (
                        False,
                        f"{name}: `--fault {fault}` is not declared by any synthetic source (declared faults: {known})",
                    )
                )
    if used and not out:
        out.append((True, f"{used} `--fault` use(s) in CI, all declared by the synthetic sources"))
    return out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def findings(project) -> list[Finding]:
    """(ok, message) of every rule, for every product; nothing runs."""
    by_table = _contracts_by_table(project)
    out: list[Finding] = []
    synthetic: list[type] = []
    for unit in project.products():
        found = registry.product_classes(unit)
        problems: list[Finding] = [(False, p) for p in found.problems]
        process_findings, bound = _process(project, unit)
        problems += process_findings
        own_tasks = {c for c in found.classes.values() if issubclass(c, Task)}
        for cls in sorted(bound | own_tasks, key=lambda c: c.__name__):
            problems += _tables(project, unit, cls, by_table)
        problems += _checks(project, unit, found.classes)
        synthetic += [c for c in found.classes.values() if issubclass(c, SyntheticSource)]
        out += problems
        if found.classes and all(ok for ok, _ in problems):
            names = ", ".join(sorted(found.classes))
            out.append((True, f"{unit.name}: extension classes declared consistently ({names})"))
    out += _cross_cutting(project)
    out += _faults(project, synthetic)
    return out
