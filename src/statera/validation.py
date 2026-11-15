"""Static validation of a content repository (runs in CI on every pull request, without data).

Groups (and the conformance level from which each one is required):

  schemas        (1) YAML/JSON against the official schemas (ODCS, ODPS, Data Package, OpenLineage)
                     and those of the statera specification
  profile        (1) contracts in the statera profile: layer consistent with the folder, urn in the
                     namespace, four-dimension classification consistent with the columns
  skeleton       (1) every product has the skeleton files (with content or `not_applicable`)
  references     (1) contracts, ports, approvals, roles, series, rules and indicators point to
                     things that exist
  layers         (2) allowed medallion dependencies in the dbt graph
  consumption    (2) queries (metadata, parameters, tests), metrics and recipes
  rules          (3) cases of the rules implemented in Python models
  classification (3) broad consumers only read publishable assets; queries that read restricted
                     data and produce broad output declare statistical disclosure control
  access         (1) dissemination products declare an access form; visualizations are complete
  processes      (3) valid, executable processes: gateways with conditions, existing
                     implementations and roles
  generated      (1-3) generated files up to date (CODEOWNERS, dbt project, unit tests, BPMN)
  hygiene        (1) no data or secrets outside the data/ folders
  conformance    (target) each product reaches the level it declares (or the one requested in --level)

`statera validate --level N` checks only what level N requires.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml
from jsonschema import Draft7Validator, Draft201909Validator, Draft202012Validator

from statera import classification, contracts, governance, ports, references
from statera.design import bpmn, processes
from statera.disseminate import registry
from statera.edition import read_jsonl
from statera.process import dbt, rules
from statera.project import ALIGNMENTS, PHASES, SCHEMAS, VISIBILITIES, Project, current, read_yaml


@dataclass
class Finding:
    group: str
    ok: bool
    message: str
    level: int = 1


VALIDATORS = {
    "odcs": ("standards/odcs-json-schema-v3.1.0.json", Draft201909Validator),
    "odps": ("standards/odps-json-schema-v1.0.0.json", Draft201909Validator),
    "datapackage": ("standards/datapackage-2.0.json", Draft7Validator),
    "openlineage": ("standards/openlineage-2-0-2.json", Draft202012Validator),
    **{
        name: (f"statera/{name}.schema.json", Draft202012Validator)
        for name in (
            "owners",
            "roles",
            "classification",
            "classification-asset",
            "calendar",
            "rule-cases",
            "process",
            "indicators",
            "controls",
            "query",
            "query-test",
            "metrics",
        )
    },
    "series": ("statera/publication-series.schema.json", Draft202012Validator),
}


@cache
def _validator(name: str):
    path, validator_class = VALIDATORS[name]
    return validator_class(json.loads((SCHEMAS / path).read_text(encoding="utf-8")))


def _read(path: Path):
    text = path.read_text(encoding="utf-8")
    return json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)


def _targets(project: Project) -> list[tuple[Path, str]]:
    targets = [
        (project.governance / "roles.yaml", "roles"),
        (project.governance / "shared-owners.yaml", "owners"),
    ]
    by_file = {
        "1-specify/product.odps.yaml": "odps",
        "1-specify/classification.yaml": "classification",
        "1-specify/owners.yaml": "owners",
        "1-specify/calendar.yaml": "calendar",
        "5-process/metrics.yaml": "metrics",
        "6-analyse/controls.yaml": "controls",
        "7-disseminate/series.yaml": "series",
        "8-evaluate/indicators.yaml": "indicators",
    }
    for u in project.units:
        targets += [(u.folder / rel, schema) for rel, schema in by_file.items() if u.has(rel)]
        targets += [(c, "odcs") for c in u.contracts()]
        targets += [(c, "process") for c in u.processes()]
    targets += [(c, "rule-cases") for folder in project.rule_folders() for c in sorted(folder.glob("*/cases.yaml"))]
    for base in project.query_folders():
        if base.is_dir():
            targets += [(c, "query") for c in sorted(base.rglob("query.yaml"))]
            targets += [(c, "query-test") for c in sorted(base.rglob("test.yaml"))]
    return targets


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


def check_schemas(project: Project) -> list[Finding]:
    findings = []
    for path, schema in _targets(project):
        try:
            errors = list(_validator(schema).iter_errors(_read(path)))
        except (yaml.YAMLError, json.JSONDecodeError) as error:
            findings.append(Finding("schemas", False, f"{project.rel(path)}: invalid syntax ({error})"))
            continue
        for e in errors[:5]:
            local = "/".join(map(str, e.absolute_path)) or "(root)"
            findings.append(Finding("schemas", False, f"{project.rel(path)} [{schema}] {local}: {e.message[:200]}"))
        if not errors:
            findings.append(Finding("schemas", True, f"{project.rel(path)} conforms to {schema}"))
    for u in project.products():
        events = read_jsonl(u.runs_folder / "events.jsonl")
        if events:
            invalid = sum(1 for ev in events if list(_validator("openlineage").iter_errors(ev)))
            findings.append(
                Finding("schemas", invalid == 0, f"{u.name}: {len(events)} OpenLineage events, {invalid} invalid")
            )
    return findings


# ---------------------------------------------------------------------------
# statera profile of the contracts
# ---------------------------------------------------------------------------

EXPECTED_LAYER = {("source", 4): "bronze", ("aggregate", 5): "silver", ("consumer", 5): "gold"}


def _expected_layer(project: Project, path: Path) -> str | None:
    unit = project.unit_of(path)
    if unit is None:
        return None
    if unit.kind == "reference":
        return "ref"
    rel = path.relative_to(unit.folder).parts[0]
    phase = next((n for n, name in PHASES.items() if name == rel), None)
    return EXPECTED_LAYER.get((unit.alignment, phase))


def check_profile(project: Project) -> list[Finding]:
    findings = []
    for cid, (path, c) in sorted(contracts.all_(project).items()):
        rel, errors = project.rel(path), []
        layer, expected_layer = contracts.layer(c), _expected_layer(project, path)
        if layer not in contracts.LAYERS:
            errors.append(f"invalid layer '{layer}'")
        elif expected_layer and layer != expected_layer:
            errors.append(f"layer '{layer}' out of place: in this folder, contracts are '{expected_layer}'")
        unit = project.unit_of(path)
        if unit and layer in contracts.LAYERS:
            urn, expected = contracts.custom_property_of(c, "urn"), contracts.expected_urn(unit, c)
            if urn != expected:
                errors.append(f"urn '{urn}' should be '{expected}'")
        cls = classification.of_table(c)
        errors += [f"classification: {e.message}" for e in _validator("classification-asset").iter_errors(cls)]
        if not errors:
            errors += classification.consistency(c)
        if not contracts.dbt_node(c) and not contracts.custom_property_of(c, "manualProduction"):
            errors.append("no dbtSource, dbtModel or dbtSeed (or manualProduction, for level 1)")
        if layer == "ref":
            errors += references.problems(c)
        if layer == "bronze" and not contracts.custom_property_of(c, "extraction"):
            errors.append("bronze contract without customProperties.extraction (extraction origin)")
        findings += [Finding("profile", False, f"{rel}: {e}") for e in errors]
        if not errors:
            findings.append(
                Finding("profile", True, f"{cid}: statera profile ({layer}, {classification.summary(cls)})")
            )
    return findings


# ---------------------------------------------------------------------------
# Cross-references
# ---------------------------------------------------------------------------


def _nodes(manifest: dict) -> tuple[dict, set]:
    models = {n["name"]: n for n in manifest["nodes"].values() if n["resource_type"] in ("model", "seed")}
    sources = {f"{s['source_name']}.{s['name']}" for s in manifest["sources"].values()}
    return models, sources


def _ref_contracts(project: Project, manifest: dict, all_: dict) -> list[Finding]:
    findings = []
    models, sources = _nodes(manifest)
    for path, c in all_.values():
        kind, name = contracts.dbt_node(c) or (None, None)
        if (kind == "source" and name not in sources) or (kind in ("model", "seed") and name not in models):
            findings.append(
                Finding("references", False, f"{project.rel(path)}: dbt{kind.title()} '{name}' does not exist")
            )
    contracted = {(contracts.dbt_node(c) or (None, None))[1] for _, c in all_.values()}
    for name, n in models.items():
        meta = (n.get("config") or {}).get("meta") or {}
        if meta.get("layer") == "gold" and name not in contracted:
            findings.append(Finding("references", False, f"gold model '{name}' without an ODCS contract"))
    findings.append(Finding("references", True, f"{len(all_)} contracts linked to dbt nodes"))
    return findings


def _ref_units(project: Project, all_: dict) -> list[Finding]:
    findings = []
    names = [u.name for u in project.units]
    if duplicates := sorted({n for n in names if names.count(n) > 1}):
        findings.append(
            Finding("references", False, f"repeated unit names (they collide in the namespace): {duplicates}")
        )
    for u in project.products():
        approval = u.read("1-specify/classification.yaml") or {}
        approved_assets = {a["contract"]: a for a in approval.get("assets", [])}
        for path in u.contracts():
            c = read_yaml(path)
            errors = classification.approval_matches(c, approved_assets.get(c["id"]))
            findings += [Finding("references", False, f"{c['id']}: {e}") for e in errors]
        for cid in sorted(set(approved_assets) - {read_yaml(c)["id"] for c in u.contracts()}):
            findings.append(
                Finding(
                    "references",
                    False,
                    f"{u.relative}: classification.yaml approves '{cid}', which does not belong to this unit",
                )
            )
        urn = None
        if u.alignment not in ALIGNMENTS or u.custom_property("visibility", "public") not in VISIBILITIES:
            findings.append(
                Finding("references", False, f"{u.name}: alignment/visibility outside {ALIGNMENTS}/{VISIBILITIES}")
            )
        if u.internal and u.alignment == "consumer":
            findings.append(
                Finding("references", False, f"{u.name}: a consumer (dissemination) product cannot be internal")
            )
        if u.kind == "product":
            odps = u.read("1-specify/product.odps.yaml")
            urn = contracts.custom_property_of(odps, "urn")
            ports_ = [p["contractId"] for p in [*odps.get("inputPorts", []), *odps.get("outputPorts", [])]]
            findings += [
                Finding("references", False, f"{u.name}: port points to nonexistent contract '{p}'")
                for p in ports_
                if p not in all_
            ]
            for p in odps.get("outputPorts", []):
                owner = project.unit_of(all_[p["contractId"]][0]) if p["contractId"] in all_ else None
                if owner and owner.name != u.name:
                    findings.append(
                        Finding(
                            "references",
                            False,
                            f"{u.name}: output '{p['name']}' is a contract of another unit ({owner.name})",
                        )
                    )
        if urn != u.urn:
            findings.append(Finding("references", False, f"{u.relative}: urn '{urn}' should be '{u.urn}'"))
    findings.append(Finding("references", True, f"{len(project.units)} units: ports, approvals and urn consistent"))
    return findings


def _role_problems(project: Project, u) -> list[str]:
    """Team authorization: existing role, purpose, legal act and unexpired validity period."""
    from datetime import date

    roles_ = governance.roles(project)
    problems = []
    for a in ports.authorized_roles(u):
        label = f"authorization of role '{a['role']}'"
        if a["role"] not in roles_:
            problems.append(f"{label}: role does not exist in governance/roles.yaml")
        problems += [f"{label}: missing '{c}'" for c in ("purpose", "until", "act") if not a.get(c)]
        if a.get("until"):
            try:
                until = date.fromisoformat(str(a["until"]))
            except ValueError:
                problems.append(f"{label}: 'until' must be a YYYY-MM-DD date")
                continue
            if until < date.today():
                problems.append(f"{label}: expired on {until}; renew it (new act) or withdraw the authorization")
    return problems


def _ref_internal_consumption(project: Project) -> list[Finding]:
    """Whoever reads an internal product must be among its authorized consumers (purpose)."""
    findings, pairs = [], 0
    for u in project.products():
        for input_ in ports.input_products(u):
            pairs += 1
            if u.name not in ports.authorized_consumers(input_):
                findings.append(
                    Finding(
                        "references",
                        False,
                        f"{u.name} reads the internal product {input_.name} without being in authorizedConsumers",
                    )
                )
        if u.internal and not ports.authorized_consumers(u):
            findings.append(Finding("references", False, f"{u.name}: internal product without authorizedConsumers"))
        findings += [Finding("references", False, f"{u.name}: {p}") for p in _role_problems(project, u)]
    from statera.consume import queries

    for c in queries.load(project):
        for input_ in ports.products_read_by_query(project, c.meta.get("bases", [])):
            pairs += 1
            if c.id not in ports.authorized_consumers(input_):
                findings.append(
                    Finding(
                        "references",
                        False,
                        f"query {c.id} reads the internal product {input_.name} without being in authorizedConsumers",
                    )
                )
    if not any(not a.ok for a in findings):
        findings.append(Finding("references", True, f"{pairs} reads of internal products, all authorized"))
    return findings


def _ref_rules(project: Project, manifest: dict) -> list[Finding]:
    findings = []
    models, _ = _nodes(manifest)
    ids = set()
    for r in rules.load(project):
        ids.add(r.id)
        if not r.folder.name.startswith(r.id):
            findings.append(
                Finding("references", False, f"{project.rel(r.folder)}: id '{r.id}' does not match the folder", 3)
            )
        if r.model not in models:
            findings.append(Finding("references", False, f"{r.id}: model '{r.model}' does not exist in dbt", 3))
    cited = set()
    for n in manifest["nodes"].values():
        meta = ((n.get("config") or {}).get("meta") or {}) | (n.get("meta") or {})
        cited.update(meta.get("rules", []))
    findings += [
        Finding("references", False, f"rule {rid} cited in dbt but without a rule folder", 3)
        for rid in sorted(cited - ids)
    ]
    findings += [
        Finding("references", False, f"rule {rid} is not cited by any dbt node", 3) for rid in sorted(ids - cited)
    ]
    findings.append(Finding("references", True, f"{len(ids)} rules with cases and an existing model", 3))
    return findings


def check_python_rules(project: Project) -> list[Finding]:
    """Cases of the rules implemented in Python models (dbt only unit-tests SQL)."""
    results = rules.run_python_cases(project)
    findings = [
        Finding("rules", False, f"{r.rule} / {r.case}: {r.message}", 3) for r in results if r.status == "failed"
    ]
    skipped = [r for r in results if r.status == "not-run"]
    if skipped:
        skipped_rules = sorted({r.rule for r in skipped})
        findings.append(Finding("rules", True, f"cases not run here ({skipped[0].message}): "
                                              f"{', '.join(skipped_rules)}; CI runs them", 3))  # fmt: skip
    if not any(not a.ok for a in findings):
        executed = len(results) - len(skipped)
        findings.append(Finding("rules", True, f"{executed} rule case(s) in Python models passed", 3))
    return findings


def _ref_roles(project: Project) -> list[Finding]:
    known = set(governance.roles(project))
    findings = [
        Finding("references", False, f"{project.rel(path)}: role '{role}' does not exist in governance/roles.yaml")
        for path, data in governance.owners(project)
        for role in sorted(governance.referenced_roles(data) - known)
    ]
    findings.append(Finding("references", True, "the owners' roles exist in the role catalog"))
    return findings


def _ref_series_indicators(project: Project) -> list[Finding]:
    from statera.evaluate import evaluation

    findings = []
    roles = set(governance.roles(project))
    for u, s in registry.series(project):
        port_names = {p["name"] for p in u.read("1-specify/product.odps.yaml").get("outputPorts", [])}
        if s["product"] != u.name:
            findings.append(
                Finding("references", False, f"{u.name}: series.yaml points to product '{s['product']}'", 2)
            )
        if s.get("main_resource") not in port_names:
            findings.append(
                Finding(
                    "references",
                    False,
                    f"{u.name}: main_resource '{s.get('main_resource')}' is not an output port",
                    2,
                )
            )
        if not (u.phase(7) / "summary.md.j2").exists():
            findings.append(Finding("references", False, f"{u.name}: missing 7-disseminate/summary.md.j2", 2))
        for c in (u.read("6-analyse/controls.yaml") or {}).get("controls", []):
            if c["port"] not in port_names:
                findings.append(
                    Finding("references", False, f"{u.name} {c['id']}: port '{c['port']}' does not exist", 3)
                )
        if u.has("8-evaluate/indicators.yaml"):
            findings += [Finding("references", False, f"{u.name}: {e}", 4) for e in evaluation.declaration_problems(u)]
            for action in evaluation.indicators(u).get("action_plan", []):
                if action["owner"] not in roles:
                    findings.append(
                        Finding(
                            "references", False, f"{u.name} {action['id']}: role '{action['owner']}' does not exist", 4
                        )
                    )
    findings.append(Finding("references", True, "series, controls and indicators linked to ports and roles", 2))
    return findings


# ---------------------------------------------------------------------------
# Layers (medallion)
# ---------------------------------------------------------------------------

ALLOWED = {  # model folder -> allowed parent types
    "staging": {"source"},
    "intermediate": {"staging", "intermediate", "seed"},
    "marts": {"intermediate", "marts", "seed"},
    "analysis": {"intermediate", "marts", "seed"},
}


def _node_type(uid: str, manifest: dict) -> str:
    if uid.startswith("source."):
        return "source"
    n = manifest["nodes"][uid]
    if n["resource_type"] in ("seed", "analysis"):
        return n["resource_type"]
    return n["fqn"][1] if len(n["fqn"]) > 2 else "model"


def check_layers(manifest: dict) -> list[Finding]:
    findings = []
    for uid, n in manifest["nodes"].items():
        if n["resource_type"] not in ("model", "analysis"):
            continue
        folder = _node_type(uid, manifest)
        for parent in n["depends_on"]["nodes"]:
            parent_type = _node_type(parent, manifest)
            if parent_type not in ALLOWED.get(folder, {parent_type}):
                findings.append(
                    Finding(
                        "layers",
                        False,
                        f"{n['name']} ({folder}) reads {parent_type} '{parent.split('.')[-1]}': forbidden",
                        2,
                    )
                )
    for e in manifest["exposures"].values():
        for parent in e["depends_on"]["nodes"]:
            if _node_type(parent, manifest) != "marts":
                findings.append(
                    Finding("layers", False, f"consumer '{e['name']}' reads outside the gold layer: {parent}", 2)
                )
    if not findings:
        findings.append(
            Finding(
                "layers",
                True,
                "staging only reads bronze; intermediate does not read gold; "
                "consumers and queries read neither bronze nor staging",
                2,
            )
        )
    return findings


# ---------------------------------------------------------------------------
# Classification (static gate)
# ---------------------------------------------------------------------------


def check_classification(project: Project, manifest: dict) -> list[Finding]:
    from statera.consume import queries

    findings = []
    by_table = contracts.by_table(project)
    by_node = {(contracts.dbt_node(c) or (None, None))[1]: c for c in by_table.values()}
    for e in manifest["exposures"].values():
        if (e.get("meta") or {}).get("sharing") != "broad":
            continue
        for parent in e["depends_on"]["nodes"]:
            c = by_node.get(manifest["nodes"][parent]["name"])
            if c is None or classification.of_table(c).get("sharing") != "broad":
                findings.append(
                    Finding(
                        "classification",
                        False,
                        f"broad consumer '{e['name']}' reads '{parent.split('.')[-1]}', which is not broad",
                        3,
                    )
                )
    for query in queries.load(project):
        output = query.meta["output_classification"]
        restricted = [
            b for b in query.meta["bases"] if classification.of_table(by_table.get(b, {})).get("sharing") != "broad"
        ]
        if output["sharing"] == "broad" and restricted and not output.get("disclosureControl"):
            findings.append(
                Finding(
                    "classification",
                    False,
                    f"{query.id}: reads {restricted} and produces broad output without disclosure control",
                    3,
                )
            )
        if output["nature"] not in ("aggregated", "deidentified") and output["sharing"] == "broad":
            findings.append(
                Finding("classification", False, f"{query.id}: broad output with nature {output['nature']}", 3)
            )
    if not findings:
        findings.append(
            Finding("classification", True, "broad consumers and queries only publish what is publishable", 3)
        )
    return findings


# ---------------------------------------------------------------------------
# Consumption: queries, metrics, recipes
# ---------------------------------------------------------------------------


def _query_relations(project: Project, manifest: dict) -> list[Finding]:
    """Queries run without dbt (statera.process.dbt.relations): its rules must give what dbt resolved."""
    static, resolved = dbt.relations(project), dbt.manifest_relations(manifest)
    read_by_queries = {
        manifest["nodes"][parent]["name"]
        for n in manifest["nodes"].values()
        if n["resource_type"] == "analysis"
        for parent in n["depends_on"]["nodes"]
        if parent in manifest["nodes"]
    }
    return [
        Finding(
            "consumption",
            False,
            f"model {name}: dbt builds {resolved[name]}, but queries run without dbt would read "
            f"{static.get(name)}; remove the schema or alias override from its config",
            2,
        )
        for name in sorted(read_by_queries)
        if name in resolved and static.get(name) != resolved[name]
    ]


def check_consumer(project: Project, manifest: dict) -> list[Finding]:
    from statera.consume import metrics, queries, recipes

    findings = []
    analyses = {n["name"] for n in manifest["nodes"].values() if n["resource_type"] == "analysis"}
    by_table = contracts.by_table(project)
    entries = queries.load(project)
    for c in entries:
        if not c.sql.exists():
            findings.append(Finding("consumption", False, f"{c.id}: missing {project.rel(c.sql)}", 2))
            continue
        if c.dbt_name not in analyses:
            findings.append(Finding("consumption", False, f"{c.id}: SQL does not appear as an analysis in dbt", 2))
        if not c.id.startswith(f"{project.namespace}.query."):
            findings.append(Finding("consumption", False, f"{c.id}: id outside the namespace {project.namespace}", 2))
        findings += [
            Finding("consumption", False, f"{c.id}: base '{b}' without a contract", 2)
            for b in c.meta["bases"]
            if b not in by_table
        ]
        failures = queries.test(c)
        findings += [Finding("consumption", False, f, 2) for f in failures]
        if not failures:
            findings.append(Finding("consumption", True, f"{c.id}: test with synthetic data passed", 2))
    for name, m in metrics.definitions(project).items():
        for source in m.get("sources", []):
            contract = by_table.get(source["table"])
            if contract is None:
                findings.append(
                    Finding("consumption", False, f"metric {name}: source {source['table']} without a contract", 2)
                )
                continue
            columns = {p["name"] for p in contract["schema"][0]["properties"]}
            missing_columns = [d for d in [m["time"], *source["dimensions"]] if d not in columns]
            findings += [
                Finding("consumption", False, f"metric {name}: {source['table']} has no column '{d}'", 2)
                for d in missing_columns
            ]
        for component in (m.get("numerator"), m.get("denominator")):
            if component and component not in metrics.definitions(project):
                findings.append(
                    Finding("consumption", False, f"metric {name}: component '{component}' does not exist", 2)
                )
    findings += _query_relations(project, manifest)
    findings += [Finding("consumption", False, p, 2) for p in recipes.header_problems(project)]
    findings.append(
        Finding(
            "consumption",
            True,
            f"{len(entries)} queries, {len(metrics.definitions(project))} metrics, "
            f"{len(recipes.load(project))} recipes",
            2,
        )
    )
    return findings


# ---------------------------------------------------------------------------
# Skeleton: the same files in every product
# ---------------------------------------------------------------------------


def check_skeleton(project: Project) -> list[Finding]:
    """Every product has the skeleton files, either with content or with `not_applicable: <reason>`."""
    from statera.project import SKELETON

    findings = []
    for u in project.products():
        for rel in SKELETON:
            reason = u.not_applicable(rel)
            if not (u.folder / rel).exists():
                findings.append(Finding("skeleton", False, f"{u.name}: missing {rel} (with content or not_applicable)"))
            elif reason is not None and len(reason.strip()) < 3:
                findings.append(Finding("skeleton", False, f"{u.name}: {rel} declares not_applicable without a reason"))
        if u.internal and u.has("7-disseminate/series.yaml"):
            findings.append(Finding("skeleton", False, f"{u.name}: an internal product is not disseminated; "
                                                    "series.yaml must declare not_applicable"))  # fmt: skip
        if u.internal and u.not_applicable("1-specify/calendar.yaml") is not None:
            findings.append(Finding("skeleton", False, f"{u.name}: an internal product's calendar is generated "
                                                    "from its consumers; run `statera sync`"))  # fmt: skip
    if not findings:
        findings.append(Finding("skeleton", True, f"{len(project.products())} products with the complete skeleton"))
    return findings


# ---------------------------------------------------------------------------
# Access: distributions and visualizations of the dissemination products
# ---------------------------------------------------------------------------


def check_access(project: Project, manifest: dict) -> list[Finding]:
    from statera import access, conformance, ports

    findings = []
    for u in project.products():
        if u.internal:
            continue
        problems = access.distribution_problems(u)
        if conformance._target(ports.odps(u)) == 0:  # draft: the access form is a level 1 requirement
            problems = [p for p in problems if not p.startswith("dissemination product with no means of access")]
        findings += [Finding("access", False, f"{u.name}: {p}") for p in problems]
    visualizations = access.visualizations(manifest)
    for e in visualizations:
        findings += [Finding("access", False, p) for p in access.visualization_problems(project, e)]
    if not any(not a.ok for a in findings):
        public_products = sum(not u.internal for u in project.products())
        findings.append(
            Finding(
                "access",
                True,
                f"{public_products} dissemination products with an access form; "
                f"{len(visualizations)} complete visualizations",
            )
        )
    return findings


# ---------------------------------------------------------------------------
# Processes, generated files, hygiene
# ---------------------------------------------------------------------------


def check_processes(project: Project, manifest: dict) -> list[Finding]:
    models = set(_nodes(manifest)[0])
    roles = set(governance.roles(project))
    findings = []
    for source in processes.sources():
        proc = processes.load(source)
        errors = processes.validate_structure(proc)
        kinds = {e["id"]: e["type"] for e in proc["elements"]}
        outgoing: dict[str, list] = {}
        for f in proc["flows"]:
            outgoing.setdefault(f["from"], []).append(f)
        executable = any((e.get("statera") or {}).get("implementation") for e in proc["elements"])
        for gid, kind in kinds.items():
            if executable and kind == "exclusiveGateway" and any(not f.get("condition") for f in outgoing.get(gid, [])):
                errors.append(
                    f"gateway {gid} has an outgoing flow without `condition` (the executor would not know the path)"
                )
        findings += [Finding("processes", False, f"{project.rel(source)}: {e}", 3) for e in errors]
    for orphan in processes.bpmn_without_source():
        findings.append(
            Finding("processes", False, f"{project.rel(orphan)} without a source YAML (statera bpmn import)", 3)
        )
    for p in bpmn.all_():
        errors = bpmn.validate(p, models, roles)
        findings += [Finding("processes", False, e, 3) for e in errors]
        if not errors:
            findings.append(
                Finding(
                    "processes",
                    True,
                    f"{project.rel(p.path)}: {len(p.tasks)} tasks linked to roles and implementations",
                    3,
                )
            )
    return findings


def _generated(ok: bool, good: str, bad: str, level: int) -> Finding:
    return Finding("generated", ok, good if ok else bad, level)


def check_generated(project: Project) -> list[Finding]:
    try:
        pending_bpmn = [project.rel(p) for p in processes.outdated()]
    except ValueError as error:  # invalid YAML: already reported in the "processes" group
        pending_bpmn = [str(error).splitlines()[0]]
    pending_dbt = [project.rel(p) for p in dbt.outdated(project)]
    from statera import release_calendar

    release_calendars = [project.rel(p) for p in release_calendar.outdated(project)]
    return [
        _generated(
            not release_calendars,
            "internal products' calendars up to date with their consumers'",
            f"calendars out of date: {', '.join(release_calendars)}: run `statera sync`",
            1,
        ),
        _generated(
            governance.codeowners_up_to_date(project),
            "CODEOWNERS up to date with the owners",
            "CODEOWNERS out of date: run `statera codeowners`",
            1,
        ),
        _generated(
            not pending_dbt,
            "dbt project up to date with the units",
            f"out of date: {', '.join(pending_dbt)}: run `statera dbt generate`",
            2,
        ),
        _generated(
            rules.is_synced(project),
            "rule unit tests in sync",
            "rule unit tests out of date: run `statera rules sync`",
            3,
        ),
        _generated(
            not pending_bpmn,
            "BPMN up to date with the process YAML",
            f"BPMN out of date relative to {', '.join(pending_bpmn)}: run `statera bpmn`",
            3,
        ),
    ]


DATA_EXTENSIONS = {".parquet", ".duckdb", ".sav", ".dta", ".xlsx", ".db"}
IGNORED = {
    "data",
    ".venv",
    ".git",
    "target",
    "logs",
    "dbt_packages",
    "node_modules",
    ".pytest_cache",
    ".ruff_cache",
    ".hypothesis",
}
SECRET_PATTERNS = [
    re.compile(p)
    for p in (
        r"(?i)(password|senha|secret|token)\s*[:=]\s*['\"][^'\"\s]{6,}",
        r"AKIA[0-9A-Z]{16}",
        r"-----BEGIN (RSA |EC )?PRIVATE KEY-----",
    )
]


def check_hygiene(project: Project) -> list[Finding]:
    findings = []
    for path in project.root.rglob("*"):
        parts = set(path.relative_to(project.root).parts)
        if parts & IGNORED or not path.is_file():
            continue
        if path.suffix in DATA_EXTENSIONS or path.name.endswith(".csv.gz"):
            findings.append(Finding("hygiene", False, f"data file outside the data/ folders: {project.rel(path)}"))
        if path.suffix in {".py", ".yml", ".yaml", ".sql", ".json", ".md", ".toml"} and path.stat().st_size < 2_000_000:
            text = path.read_text(encoding="utf-8", errors="ignore")
            if any(p.search(text) for p in SECRET_PATTERNS):
                findings.append(Finding("hygiene", False, f"possible secret in {project.rel(path)}"))
    if not findings:
        findings.append(Finding("hygiene", True, "no data files or secrets outside the data/ folders"))
    return findings


def check_conformance(project: Project, level: int | None) -> list[Finding]:
    from statera import conformance

    findings = []
    for r in conformance.evaluate(project, level):
        ok = r.achieved >= r.target
        text = f"{r.unit.name}: level {r.achieved} ({conformance.NAMES.get(r.achieved, 'none')}), target {r.target}"
        findings.append(Finding("conformance", ok, text, r.target))
        if not ok:
            findings += [
                Finding("conformance", False, f"{r.unit.name} level {n}: {m}", r.target)
                for n, m in r.outstanding
                if n <= r.target
            ]
    return findings


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


def collect_findings(generate_manifest: bool = True, level: int | None = None) -> list[Finding]:
    project = current()
    m = dbt.manifest(generate=generate_manifest, project=project)
    steps: list[Callable[[], list[Finding]]] = [
        lambda: check_schemas(project),
        lambda: check_profile(project),
        lambda: check_skeleton(project),
        lambda: _ref_contracts(project, m, contracts.all_(project)),
        lambda: _ref_units(project, contracts.all_(project)),
        lambda: _ref_internal_consumption(project),
        lambda: _ref_rules(project, m),
        lambda: _ref_roles(project),
        lambda: _ref_series_indicators(project),
        lambda: check_layers(m),
        lambda: check_python_rules(project),
        lambda: check_consumer(project, m),
        lambda: check_classification(project, m),
        lambda: check_access(project, m),
        lambda: check_processes(project, m),
        lambda: check_generated(project),
        lambda: check_hygiene(project),
        lambda: check_conformance(project, level),
    ]
    findings = [a for step in steps for a in step()]
    return [a for a in findings if level is None or a.level <= level]


def run(generate_manifest: bool = True, verbose: bool = False, level: int | None = None) -> int:
    findings = collect_findings(generate_manifest, level)
    groups: dict[str, list[Finding]] = {}
    for a in findings:
        groups.setdefault(a.group, []).append(a)
    if level:
        print(f"Validation at conformance level {level}\n")
    for group, entries in groups.items():
        failures = [a for a in entries if not a.ok]
        print(f"{'✔' if not failures else '✘'} {group:<14} {len(entries) - len(failures)} ok, {len(failures)} failures")
        for a in entries:
            if not a.ok or verbose:
                print(f"    {'ok  ' if a.ok else 'FAIL'} {a.message}")
    total = sum(not a.ok for a in findings)
    print(f"\n{'Repository is valid.' if not total else f'{total} problem(s) found.'}")
    return 1 if total else 0
