"""Conformance levels of a product (specification v0, `08-conformance-levels.md`).

Dissemination product (consumer-aligned):
    1 Catalogued     product specified (ODPS), output contracts in the statera profile, approved
                     classification, owners, where the public accesses it (series or declared
                     distributions)
    2 Reproducible   calendar with cut-off date, publication series, outputs built by versioned
                     code (dbt models), recurring queries catalogued
    3 Controlled     business rules with executable cases (in the product or in the internal products
                     it depends on), controls, open publication gate, executable process, lineage
                     down to bronze
    4 Evaluated      declared and computable indicators, action plan, parity with the legacy

Internal product (source-aligned or aggregate): instead of the series and the publication gate, it
requires authorized consumers, extraction origin and retention (source) or partitioning by cut
(aggregate), and rules with cases (aggregate).

Each product declares its target level in `product.odps.yaml` (customProperties.conformanceLevel).
The ACHIEVED level is the largest N such that all requirements from 1 to N are met. Requirements
are checked without data (they run in any CI).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from statera import classification, contracts, ports
from statera.project import Project, Unit, current

NAMES = {1: "catalogued", 2: "reproducible", 3: "controlled", 4: "evaluated"}


@dataclass
class Result:
    unit: Unit
    target: int
    achieved: int
    outstanding: list[tuple[int, str]] = field(default_factory=list)


def _target(odps: dict) -> int:
    return int(contracts.custom_property_of(odps, "conformanceLevel", 1))


def _outputs(u: Unit, ctx: dict) -> list[tuple[dict, dict]]:
    """(port, contract) for each output port whose contract exists."""
    all_ = ctx["contracts"]
    return [(p, all_[p["contractId"]][1]) for p in ports.output_ports(u) if p["contractId"] in all_]


# ---------------------------------------------------------------------------
# Common
# ---------------------------------------------------------------------------


def _level1(u: Unit, ctx: dict) -> list[str]:
    files = ("product.odps.yaml", "classification.yaml", "owners.yaml")
    pend = [f"missing 1-specify/{a}" for a in files if not (u.phase(1) / a).exists()]
    if pend:
        return pend
    approved_assets = {a["contract"]: a for a in (u.read("1-specify/classification.yaml") or {}).get("assets", [])}
    for p in ports.output_ports(u):
        if p["contractId"] not in ctx["contracts"]:
            pend.append(f"output {p['name']}: contract {p['contractId']} does not exist")
    for _, c in _outputs(u, ctx):
        if not contracts.custom_property_of(c, "urn"):
            pend.append(f"{c['id']}: no urn (namespace)")
        pend += [f"{c['id']}: {e}" for e in classification.consistency(c)]
        pend += [f"{c['id']}: {e}" for e in classification.approval_matches(c, approved_assets.get(c["id"]))]
    if u.internal and not ports.authorized_consumers(u):
        pend.append("internal product without authorizedConsumers (who may read it and for what purpose)")
    if not u.internal:
        from statera import access

        pend += access.distribution_problems(u)
    return pend


def _missing(u: Unit, relative: str, label: str) -> list[str]:
    """Pending item for a skeleton file: missing, or present but declaring that it does not apply."""
    if u.has(relative):
        return []
    if (reason := u.not_applicable(relative)) is not None:
        return [f"{relative} declares it does not apply ({reason}); the level requires {label}"]
    return [f"missing {relative} ({label})"]


def _executable_process(u: Unit) -> list[str]:
    if missing := _missing(u, "2-design/process.yaml", "executable process"):
        return missing
    if not u.process.with_suffix(".bpmn").exists():
        return ["process.bpmn not generated (statera bpmn)"]
    return []


def _has_rules(u: Unit, ctx: dict) -> bool:
    return any(r.unit and r.unit.name in ctx["chain"][u.name] for r in ctx["rules"])


# ---------------------------------------------------------------------------
# Dissemination product
# ---------------------------------------------------------------------------


def _consumer2(u: Unit, ctx: dict) -> list[str]:
    pend = []
    pend += _missing(u, "1-specify/calendar.yaml", "cut-off dates")
    pend += _missing(u, "7-disseminate/series.yaml", "publication series")
    for p, c in _outputs(u, ctx):
        node = contracts.dbt_node(c)
        if not node or node[1] not in ctx["models"]:
            pend.append(f"output {p['name']}: not built by a dbt model in the repository")
    if not any(c.meta.get("product") == u.name for c in ctx["queries"]):
        pend.append("no recurring query catalogued for the product")
    return pend


def _consumer3(u: Unit, ctx: dict) -> list[str]:
    pend = []
    if not _has_rules(u, ctx):
        pend.append("no rule with executable cases in the product or in the internal products it depends on")
    if not u.has("6-analyse/controls.yaml") and not any((u.phase(6) / "dbt-tests").glob("*.sql")):
        pend.append("no controls (6-analyse/controls.yaml or dbt-tests/)")
    for p, c in _outputs(u, ctx):
        control = classification.required_control(c) or {}
        unpublished = control.get("unpublishedColumns", [])
        published_columns = [x["name"] for x in c["schema"][0]["properties"] if x["name"] not in unpublished]
        reasons = classification.publication_blockers(c, published_columns, applied_control=bool(control))
        pend += [f"output {p['name']} would not pass the publication gate: {m}" for m in reasons]
    pend += _executable_process(u)
    pend += [f"output {m}: lineage does not reach a bronze source" for m in ctx["without_bronze"].get(u.name, [])]
    return pend


def _consumer4(u: Unit, ctx: dict) -> list[str]:
    from statera.evaluate import evaluation

    if missing := _missing(u, "8-evaluate/indicators.yaml", "quality indicators"):
        return missing
    pend = list(evaluation.declaration_problems(u))
    if not evaluation.indicators(u).get("action_plan"):
        pend.append("indicators.yaml without action_plan (GSBPM 8.3)")
    parity = contracts.custom_property_of(ports.odps(u), "legacyParity", "not-declared")
    if parity not in ("done", "not-applicable"):
        pend.append(f"legacy parity: {parity}")
    return pend


# ---------------------------------------------------------------------------
# Internal product (source-aligned or aggregate)
# ---------------------------------------------------------------------------


def _internal2(u: Unit, ctx: dict) -> list[str]:
    pend = []
    for p, c in _outputs(u, ctx):
        if u.alignment == "source":
            if not contracts.custom_property_of(c, "extraction"):
                pend.append(f"output {p['name']}: contract without customProperties.extraction")
            if not any(s.get("property") == "retention" for s in c.get("slaProperties", []) or []):
                pend.append(f"output {p['name']}: contract without declared retention (slaProperties.retention)")
            continue
        node = contracts.dbt_node(c)
        if not node or node[1] not in ctx["models"]:
            pend.append(f"output {p['name']}: not built by a dbt model in the repository")
        if not contracts.custom_property_of(c, "partitionedBy"):
            pend.append(f"output {p['name']}: does not keep the cuts (customProperties.partitionedBy)")
    return pend


def _internal3(u: Unit, ctx: dict) -> list[str]:
    pend = _executable_process(u)
    for p, c in _outputs(u, ctx):
        if not c["schema"][0].get("quality"):
            pend.append(f"output {p['name']}: contract without quality rules")
    if u.alignment == "aggregate":
        if not _has_rules(u, ctx):
            pend.append("no business rule with executable cases")
        pend += [f"output {m}: lineage does not reach a bronze source" for m in ctx["without_bronze"].get(u.name, [])]
    return pend


def _internal4(u: Unit, ctx: dict) -> list[str]:
    return _missing(u, "8-evaluate/indicators.yaml", "service indicators: delay, completeness")


REQUIREMENTS: dict[int, Callable[[Unit, dict], list[str]]] = {1: _level1, 2: _consumer2, 3: _consumer3, 4: _consumer4}
INTERNAL_REQUIREMENTS: dict[int, Callable[[Unit, dict], list[str]]] = {
    1: _level1, 2: _internal2, 3: _internal3, 4: _internal4,
}  # fmt: skip


# ---------------------------------------------------------------------------
# Context (computed once) and evaluation
# ---------------------------------------------------------------------------


def _chain(u: Unit) -> set[str]:
    names = {u.name}
    for input_ in ports.input_products(u):
        names |= _chain(input_)
    return names


def _context(project: Project) -> dict:
    from statera.consume import queries
    from statera.process import dbt, rules

    manifest = dbt.manifest(generate=False, project=project)
    nodes = manifest["nodes"]
    models = {n["name"]: uid for uid, n in nodes.items() if n["resource_type"] in ("model", "seed")}

    def reaches_bronze(uid: str, seen: set) -> bool:
        if uid.startswith("source."):
            return True
        if uid in seen or uid not in nodes:
            return False
        seen.add(uid)
        return any(reaches_bronze(p, seen) for p in nodes[uid]["depends_on"]["nodes"])

    all_ = contracts.all_(project)
    without_bronze = {}
    for u in project.products():
        missing_products = []
        for p in ports.output_ports(u):
            c = all_.get(p["contractId"], (None, {}))[1]
            node = contracts.dbt_node(c) if c else None
            if node and node[0] == "model" and node[1] in models and not reaches_bronze(models[node[1]], set()):
                missing_products.append(p["name"])
        without_bronze[u.name] = missing_products
    return {
        "contracts": all_,
        "models": set(models),
        "queries": queries.load(project),
        "rules": rules.load(project),
        "without_bronze": without_bronze,
        "chain": {u.name: _chain(u) for u in project.products()},
    }


def evaluate(project: Project | None = None, forced_level: int | None = None) -> list[Result]:
    project = project or current()
    ctx = _context(project)
    results = []
    for u in project.products():
        requirements = INTERNAL_REQUIREMENTS if u.internal else REQUIREMENTS
        target = forced_level or _target(ports.odps(u))
        achieved, outstanding = 0, []
        for level in sorted(requirements):
            gaps = requirements[level](u, ctx)
            outstanding += [(level, f) for f in gaps]
            if gaps:
                break
            achieved = level
        results.append(Result(u, target, achieved, outstanding))
    return results
