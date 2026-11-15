"""Catalog of ODCS v3.1 contracts and the statera profile on top of them.

Contracts live with the unit and the phase that PRODUCE the dataset:

    products/<source>/4-collect/contracts/      bronze (extractions; source-aligned internal product)
    products/<aggregate>/5-process/contracts/   silver (canonical entities; aggregate internal product)
    products/<consumer>/5-process/contracts/    gold (dissemination tables)
    references/<r>/                             ref (official reference tables)

statera profile (specification v0, `04-profiles.md`): besides the official ODCS schema, every
contract declares in `customProperties`:

    layer           bronze | silver | gold | ref
    urn             <namespace>.<unit>.<layer>.<table with hyphens>
    classification  object with the four dimensions (see classification.py)
    dbtSource | dbtModel | dbtSeed   the dbt node that materializes the table
"""

from __future__ import annotations

from pathlib import Path

from statera.project import Project, Unit, current, read_yaml

LAYERS = ("bronze", "silver", "gold", "ref")


def custom_property_of(obj: dict, name: str, default=None):
    for item in obj.get("customProperties", []) or []:
        if item.get("property") == name:
            return item.get("value")
    return default


def files(project: Project | None = None) -> list[Path]:
    project = project or current()
    return [c for u in project.units for c in u.contracts()]


def all_(project: Project | None = None) -> dict[str, tuple[Path, dict]]:
    """All contracts in the repository, indexed by id."""
    result = {}
    for path in files(project):
        data = read_yaml(path)
        result[data["id"]] = (path, data)
    return result


def layer(contract: dict) -> str | None:
    return custom_property_of(contract, "layer")


def physical_table(contract: dict) -> str:
    return contract["schema"][0]["physicalName"]


def expected_urn(unit: Unit, contract: dict) -> str:
    table = physical_table(contract).split(".")[-1].replace("_", "-")
    return f"{unit.urn}.{layer(contract)}.{table}"


def by_table(project: Project | None = None) -> dict[str, dict]:
    """Contract of each physical table ('gold.sales_monthly' -> contract)."""
    return {physical_table(c): c for _, c in all_(project).values()}


def dbt_node(contract: dict) -> tuple[str, str] | None:
    """('source'|'model'|'seed', name) of the dbt node that materializes the table."""
    for key, kind in (("dbtSource", "source"), ("dbtModel", "model"), ("dbtSeed", "seed")):
        value = custom_property_of(contract, key)
        if value:
            return kind, value
    return None
