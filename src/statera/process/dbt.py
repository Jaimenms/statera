"""The organization's dbt project: generated from the units, run by the framework.

The content repository has ONE dbt project, with `dbt_project.yml` at the root (ADR 0017) and the
models in the units:

    products/<source>/5-process/models/     staging (reading bronze at the cut)
    products/<aggregate>/5-process/models/  intermediate (canonical silver, per cut)
    products/<consumer>/5-process/models/   intermediate and marts (gold)
    products/<p>/6-analyse/dbt-tests/       SQL controls of the edition (singular tests)
    references/<r>/                         seeds (ref)
    queries/, products/<p>/queries/         dbt analyses (recurring queries)

GENERATED files (CI checks that they are up to date):
    dbt_project.yml                  at the root: unit paths and medallion conventions
    dbt/generated/_references.yml    seed properties, taken from the reference table contracts
    dbt/generated/_groups.yml        one dbt group per product (owner), for access control

Access control between products (dbt Core: groups and access): the models of an INTERNAL product
are `private` to its group, except those that materialize an output port (and the staging models of a
source-aligned product), which are `public`. A consumer that tries to read another product's private
model is blocked by dbt at compile time.
    dbt/generated/_rule_unit_tests.yml  (process/rules.py)

The generic macros come from the `statera` dbt package (packages.yml, at the root), not from a copy.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

from statera import classification, contracts, generated
from statera.project import DBT_MACROS, REFERENCE_COLOR, Project, current, read_yaml

DBT_LAYERS = {
    "staging": {"+schema": "silver", "+materialized": "view", "+meta": {"layer": "silver"}},
    "intermediate": {"+schema": "silver", "+materialized": "table", "+meta": {"layer": "silver"}},
    "marts": {"+schema": "gold", "+materialized": "table", "+meta": {"layer": "gold"}},
}
SEED_TYPES = {"string": "varchar", "integer": "integer", "number": "double", "date": "date", "boolean": "boolean"}


def _rel(project: Project, folder: Path) -> str:
    return os.path.relpath(folder, project.dbt_project).replace(os.sep, "/")


def _existing(project: Project, folders) -> list[str]:
    return [_rel(project, p) for p in folders if p.is_dir()]


def generate_project(project: Project | None = None) -> str:
    project = project or current()
    units = project.products()
    content = {
        "name": project.dbt_name,
        "version": "0.1.0",
        "config-version": 2,
        "profile": project.dbt_profile,
        "model-paths": [
            *_existing(project, [u.phase(5) / "models" for u in units]),
            _rel(project, project.dbt_folder / "generated"),
        ],
        "seed-paths": _existing(project, [r.folder for r in project.references()]),
        "test-paths": _existing(project, [u.phase(6) / "dbt-tests" for u in units]),
        "macro-paths": [
            _rel(project, project.dbt_folder / "macros"),
            *_existing(project, [u.phase(5) / "macros" for u in units]),
        ],
        "analysis-paths": _existing(project, project.query_folders()),
        "docs-paths": [_rel(project, project.dbt_folder / "docs")],
        "target-path": "target",
        "packages-install-path": _rel(project, project.dbt_folder / "dbt_packages"),
        "clean-targets": ["target", "logs", _rel(project, project.dbt_folder / "dbt_packages")],
        "vars": {"cut_date": "2099-12-31", "period": "2099-12", "batches": {}, **project.conventions},
        "seeds": {project.dbt_name: _seed_configs(project)},
        "models": {project.dbt_name: _model_configs(project)},
        "exposures": {project.dbt_name: _exposure_configs(project)},
        "data_tests": {project.dbt_name: {"+severity": "error"}},
    }
    if not content["exposures"][project.dbt_name]:
        del content["exposures"]
    notice = generated.header(
        "statera dbt generate",
        "statera.toml and the units (models, references, tests, macros and queries)",
        note="Medallion: bronze = sources (dated extractions); silver = staging + intermediate; gold = marts;\n"
        "ref = seeds. The variables are overridden in each edition by `statera run`.",
    )
    return notice + yaml.safe_dump(content, allow_unicode=True, sort_keys=False, width=110)


def _product_models(unit) -> list[tuple[list[str], str]]:
    """(path parts inside models/, model name) of each model of the product, in SQL or in
    Python (dbt Python models: `def model(dbt, session)`, with pandas)."""
    base = unit.phase(5) / "models"
    files = [*base.rglob("*.sql"), *(p for p in base.rglob("*.py") if not p.name.startswith("_"))]
    return [(list(a.relative_to(base).parent.parts), a.stem) for a in sorted(files)]


def _access(unit, parts: list[str], model: str, public_products: set[str]) -> str | None:
    if not unit.internal:
        return None  # consumer: dbt default (protected)
    if model in public_products or (parts and parts[0] == "staging"):
        return "public"
    return "private"


def _contract_meta(contract: dict) -> dict:
    """What the catalog knows about the table, in the model's `meta`: it shows up (and is searchable) in dbt docs."""
    return {
        "contract": contract["id"],
        "urn": contracts.custom_property_of(contract, "urn"),
        "classification": classification.summary(classification.of_table(contract)),
    }


def _grants(project: Project, unit, model: str, outputs: set[str]) -> dict | None:
    """Read permission in the engine on an internal product's outputs for the authorized teams
    (`authorizedConsumers` with `role`, within their validity period): the real boundary, generated from
    the same declaration that validation checks. The demo DuckDB has no roles: only in other engines."""
    from statera import ports

    if project.engine["type"] == "duckdb" or not unit.internal or model not in outputs:
        return None
    roles = sorted({a["role"] for a in ports.authorized_roles(unit) if ports.current_authorization(unit, a["role"])})
    return {"select": roles} if roles else None


def product_tags(unit) -> list[str]:
    """dbt tags of every node of a product: select one product (`tag:product:x`, `+tag:product:x+`) or
    one alignment in dbt docs, the dbt CLI and any tool that reads the manifest."""
    return [f"product:{unit.name}", f"alignment:{unit.alignment}"]


def _seed_configs(project: Project) -> dict:
    """Reference tables: schema `ref`, tag `reference` (+ `reference:<name>`) and the gray of references."""
    tree = {"+schema": "ref", "+tags": ["reference"], "+docs": {"node_color": REFERENCE_COLOR}}
    for ref in project.references():
        for path in ref.contracts():
            for obj in read_yaml(path).get("schema", []):
                tree[obj["name"]] = {"+tags": [f"reference:{ref.name}"]}
    return tree


def _exposure_configs(project: Project) -> dict:
    """Tags of the product's exposures, configured by their path (dbt fqn: the folders of the .yml inside
    models/, then the exposure name). dbt applies no dbt_project.yml tags to sources or analyses: select a
    product with its sources and queries as `+tag:product:<name>+` (parents and children of its models)."""
    tree: dict = {}
    for unit in project.products():
        base = unit.phase(5) / "models"
        for yml in sorted(base.rglob("*.yml")) if base.is_dir() else []:
            for entry in (read_yaml(yml) or {}).get("exposures") or []:
                node = tree
                for part in [*yml.relative_to(base).parent.parts, entry["name"]]:
                    node = node.setdefault(part, {})
                node["+tags"] = product_tags(unit)
    return tree


def _model_configs(project: Project) -> dict:
    """Configuration per layer (medallion) and, per model, the group (product), the tags and color of the
    product, the access and the contract."""
    tree = {layer: dict(conf) for layer, conf in DBT_LAYERS.items()}
    colors = project.product_colors()
    for unit in project.products():
        by_model = {}
        for path in unit.contracts():
            c = read_yaml(path)
            if model := contracts.custom_property_of(c, "dbtModel"):
                by_model[model] = c
        for parts, model in _product_models(unit):
            node = tree
            for part in parts:
                node = node.setdefault(part, {})
            config = {"+group": unit.name, "+tags": product_tags(unit), "+docs": {"node_color": colors[unit.name]}}
            if access := _access(unit, parts, model, set(by_model)):
                config["+access"] = access
            if model in by_model:
                config["+meta"] = {"product": unit.name, **_contract_meta(by_model[model])}
            if grants := _grants(project, unit, model, set(by_model)):
                config["+grants"] = grants
            node[model] = config
    return tree


def relations(project: Project | None = None) -> dict[str, str]:
    """Physical relation (`schema.name`) of each model and reference table, WITHOUT dbt: the same rules
    the generated dbt_project.yml gives dbt (schema by layer folder; seeds in `ref`). Lets someone who only
    reads the data run the cataloged queries without installing dbt. Validation compares it with dbt's
    manifest, so a model that overrides its schema or alias in its own config is caught in CI."""
    project = project or current()
    output = {}
    for unit in project.products():
        for parts, model in _product_models(unit):
            layer = DBT_LAYERS.get(parts[0]) if parts else None
            output[model] = f"{layer['+schema'] if layer else 'main'}.{model}"
    for ref in project.references():
        for seed in sorted(ref.folder.glob("*.csv")):
            output[seed.stem] = f"ref.{seed.stem}"
    return output


def manifest_relations(manifest: dict) -> dict[str, str]:
    """Physical relation of each model and seed, as dbt resolved it."""
    return {
        n["name"]: f"{n['schema']}.{n.get('alias') or n['name']}"
        for n in manifest["nodes"].values()
        if n["resource_type"] in ("model", "seed")
    }


def generate_groups(project: Project | None = None) -> str:
    project = project or current()
    groups = [
        {"name": u.name, "owner": {"name": (u.odps.get("team") or {}).get("name", u.name)},
         "description": f"Product {u.name} ({u.alignment}, {'internal' if u.internal else 'published'})"}
        for u in project.products()
    ]  # fmt: skip
    body = yaml.safe_dump({"version": 2, "groups": groups}, allow_unicode=True, sort_keys=False, width=110)
    return generated.header("statera dbt generate", "products/*/1-specify/product.odps.yaml") + body


def _seed(contract: dict) -> dict:
    obj = contract["schema"][0]
    columns, kinds = [], {}
    for prop in obj["properties"]:
        physical_type = (prop.get("physicalType") or "").lower()
        kinds[prop["name"]] = physical_type or SEED_TYPES[prop["logicalType"]]
        tests = []
        if prop.get("primaryKey") and _simple_key(obj) and prop["name"] != _valid_from():
            tests += ["unique", "not_null"]
        for rel in prop.get("relationships", []) or []:
            table, field = rel["to"].split(".")
            tests.append({"relationships": {"arguments": {"to": f"ref('{table}')", "field": field}}})
        if tests:
            columns.append({"name": prop["name"], "data_tests": tests})
    seed = {
        "name": obj["name"],
        "description": contract["description"]["purpose"],
        "config": {"column_types": kinds},
        "meta": {
            "layer": "ref",
            "contract": contract["id"],
            "urn": contracts.custom_property_of(contract, "urn"),
            "authority": contracts.custom_property_of(contract, "authority"),
        },
    }
    if columns:
        seed["columns"] = columns
    if contracts.custom_property_of(contract, "temporality") == "validity":
        key = next((p["name"] for p in obj["properties"] if p.get("primaryKey") and p["name"] != _valid_from()), None)
        if key:
            seed["data_tests"] = [{"statera.non_overlapping_validity": {"arguments": {"key": key}}}]
    return seed


def _valid_from() -> str:
    return current().conventions["valid_from_column"]


def _simple_key(obj: dict) -> bool:
    return sum(1 for p in obj["properties"] if p.get("primaryKey")) == 1


def generate_references(project: Project | None = None) -> str:
    project = project or current()
    seeds = [_seed(read_yaml(c)) for r in project.references() for c in r.contracts()]
    body = yaml.safe_dump({"version": 2, "seeds": seeds}, allow_unicode=True, sort_keys=False, width=110)
    return generated.header("statera dbt generate", "references/*/*.odcs.yaml (reference table contracts)") + body


def generated_files(project: Project | None = None) -> dict[Path, str]:
    project = project or current()
    return {
        project.dbt_project / "dbt_project.yml": generate_project(project),
        project.dbt_folder / "generated" / "_references.yml": generate_references(project),
        project.dbt_folder / "generated" / "_groups.yml": generate_groups(project),
    }


def sync(project: Project | None = None) -> list[Path]:
    written = []
    if write_packages(project):
        written.append((project or current()).dbt_project / "packages.yml")
    for path, text in generated_files(project).items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


def outdated(project: Project | None = None) -> list[Path]:
    return [
        c for c, text in generated_files(project).items() if not c.exists() or c.read_text(encoding="utf-8") != text
    ]


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


class ProductionToolsMissing(RuntimeError):
    """dbt is not in this environment (someone who only reads the data does not need it)."""


MISSING_DBT = (
    "dbt is not installed in this environment. Reading the data does not need it; producing does. "
    "The production tools are the `produce` extra: in the data repository's pyproject.toml, "
    '[dependency-groups] produce = ["statera[produce]"]; then any production command (statera check, run, '
    "sync…) installs them, or `uv sync --all-groups`."
)


def run(arguments: list[str], variables: dict | None = None, project: Project | None = None):
    """Runs dbt in a subprocess (isolates dbt's DuckDB connection from the Python process's)."""
    project = project or current()
    dbt = Path(sys.executable).parent / ("dbt.exe" if os.name == "nt" else "dbt")
    if not dbt.exists():
        raise ProductionToolsMissing(MISSING_DBT)
    command = [
        str(dbt),
        *arguments,
        "--project-dir",
        str(project.dbt_project),
        "--profiles-dir",
        str(project.dbt_project),
    ]
    if variables:
        command += ["--vars", json.dumps(variables)]
    project.warehouse.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "STATERA_DUCKDB": str(project.warehouse), "DBT_SEND_ANONYMOUS_USAGE_STATS": "false"}
    return subprocess.run(command, cwd=project.dbt_project, env=env, capture_output=True, text=True)


def generate_packages(project: Project | None = None) -> str:
    """packages.yml of the dbt project: the framework's dbt package is the one shipped INSIDE the installed
    Python package. So there is only one version to pin (the Python package's, in pyproject.toml) and no
    extra credentials. The path depends on the machine (virtual environment), so the file is generated
    and kept out of git."""
    project = project or current()
    local = os.path.relpath(DBT_MACROS, project.dbt_project).replace(os.sep, "/")
    notice = generated.header(
        "statera sync",
        "the statera package installed in the environment (pyproject.toml)",
        ci=False,
        note="Kept out of git (.gitignore): the path is this machine's virtual environment.",
    )
    return notice + f"packages:\n  - local: {local}\n"


def write_packages(project: Project | None = None) -> bool:
    """Writes packages.yml if it changed; returns True when it changed (dbt deps must be run again)."""
    project = project or current()
    destination, text = project.dbt_project / "packages.yml", generate_packages(project)
    if destination.exists() and destination.read_text(encoding="utf-8") == text:
        return False
    destination.write_text(text, encoding="utf-8")
    return True


def ensure_packages(project: Project | None = None) -> None:
    """`dbt deps` when the framework package is not yet installed in the project (or has moved)."""
    project = project or current()
    if not (project.dbt_project / "dbt_project.yml").exists():  # fresh repository: nothing generated yet
        sync(project)
    if write_packages(project) or not (project.dbt_folder / "dbt_packages" / "statera").exists():
        process = run(["deps"], project=project)
        if process.returncode != 0:
            raise RuntimeError(f"dbt deps failed:\n{process.stdout[-2000:]}")


def manifest(generate: bool = True, project: Project | None = None) -> dict:
    """dbt manifest (`dbt parse` needs no data)."""
    project = project or current()
    if generate or not project.dbt_manifest.exists():
        ensure_packages(project)
        process = run(["parse"], project=project)
        if process.returncode != 0:
            raise RuntimeError(f"dbt parse failed:\n{process.stdout[-3000:]}")
    return json.loads(project.dbt_manifest.read_text())


def results(project: Project | None = None) -> list[dict]:
    path = (project or current()).dbt_results
    return json.loads(path.read_text())["results"] if path.exists() else []


def failures(results_: list[dict]) -> list[str]:
    return [
        f"{r['unique_id']}: {r['status']} — {(r.get('message') or '').strip()[:300]}"
        for r in results_
        if r["status"] in ("error", "fail")
    ]


def test_count(results_: list[dict]) -> tuple[int, int]:
    tests = [r for r in results_ if r["unique_id"].startswith(("test.", "unit_test."))]
    return len(tests), sum(r["status"] in ("fail", "error") for r in tests)
