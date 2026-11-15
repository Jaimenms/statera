"""Reference tables over time: declared temporality and the versions used by each edition or cut.

Reference tables (municipalities, CNAE, CBO, minimum wage…) change: a new municipality is installed,
CNAE gets a new version, CBO is updated. The contract of each reference table declares its TEMPORALITY
(`customProperties.temporality`, specification 09):

    stable     the same table holds for the whole period (e.g. major regions)
    validity   each row holds within an interval [valid_from, valid_to] (validity period); joins use
               the event date (e.g. municipalities by installation date, minimum wage)
    versioned  the reference table is published in versions (releases) that do not mix; the consumer
               pins the version and, across versions, uses the authority's correspondence table
               (e.g. CNAE 2.0 -> 2.3, CBO)

And every published edition and every internal product cut records the contract version and the
content hash of each reference table used, just as they record the source cut. A reference table used
INSIDE an input internal product comes from the cut manifest (the hash at the moment the cut was
computed, field `via`), not from the current file: the consumer reads the cut partition, it does not
recompute it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from statera import contracts
from statera.project import Project, Unit, current

TEMPORALITIES = ("stable", "validity", "versioned")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _in_product(project: Project, unit: Unit, node: dict) -> bool:
    path = (project.dbt_project / node.get("original_file_path", "")).resolve()
    return unit.folder in path.parents


def _from_other_product(project: Project, unit: Unit, node: dict) -> bool:
    owner = project.unit_of(project.dbt_project / node.get("original_file_path", ""))
    return owner is not None and owner.kind == "product" and owner != unit


def _from_cuts(cuts: dict) -> list[dict]:
    """Reference tables recorded in the cut manifests of the input products (and of those upstream of them)."""
    output = []
    for name, manifest in (cuts or {}).items():
        output += [{**r, "via": r.get("via") or name} for r in manifest.get("references") or []]
        output += _from_cuts(manifest.get("inputs") or {})
    return output


def used(unit: Unit, manifest: dict, cuts: dict | None = None) -> list[dict]:
    """Reference tables (seeds) the unit depends on, with version, temporality and hash.

    Those used by the unit's own models come from the current file; those used inside the input
    internal products come from the manifests of the pinned cuts (`cuts`), with the `via` field.
    """
    project = unit.project
    nodes = manifest["nodes"]
    stack = [uid for uid, n in nodes.items() if n["resource_type"] == "model" and _in_product(project, unit, n)]
    seen, seeds = set(), set()
    while stack:
        uid = stack.pop()
        if uid in seen or uid not in nodes:
            continue
        seen.add(uid)
        if nodes[uid]["resource_type"] == "seed":
            seeds.add(uid)
            continue
        if _from_other_product(project, unit, nodes[uid]):
            continue  # read from the cut partition: its reference tables are in the cut manifest
        stack += nodes[uid]["depends_on"]["nodes"]
    by_seed = {contracts.custom_property_of(c, "dbtSeed"): c for _, c in contracts.all_(project).values()}
    output = []
    for uid in sorted(seeds):
        node = nodes[uid]
        contract = by_seed.get(node["name"], {})
        file = (project.dbt_project / node["original_file_path"]).resolve()
        output.append({
            "reference": node["name"],
            "contract": contract.get("id"),
            "urn": contracts.custom_property_of(contract, "urn") if contract else None,
            "version": contract.get("version"),
            "temporality": contracts.custom_property_of(contract, "temporality", "stable") if contract else None,
            "authority": contracts.custom_property_of(contract, "authority") if contract else None,
            "sha256": _sha256(file) if file.exists() else None,
            "via": None,
        })  # fmt: skip
    seen_pairs = {(r["reference"], r["via"]) for r in output}
    for r in _from_cuts(cuts):
        if (r["reference"], r["via"]) not in seen_pairs:
            seen_pairs.add((r["reference"], r["via"]))
            output.append(r)
    return output


def problems(contract: dict) -> list[str]:
    """Consistency of the declared temporality with the reference table's columns."""
    temporality = contracts.custom_property_of(contract, "temporality")
    if temporality is None:
        return ["no customProperties.temporality (stable, validity or versioned)"]
    if temporality not in TEMPORALITIES:
        return [f"temporality '{temporality}' not in {list(TEMPORALITIES)}"]
    columns = {p["name"] for p in contract["schema"][0]["properties"]}
    conv = current().conventions
    start, end = conv["valid_from_column"], conv["valid_to_column"]
    if temporality == "validity" and not {start, end} <= columns:
        return [f"temporality 'validity' requires the columns {start} and {end} ([conventions] in statera.toml)"]
    return []
