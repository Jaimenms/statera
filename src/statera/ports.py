"""Output ports of a product (ODPS) and the tables they publish (ODCS contracts)."""

from __future__ import annotations

from datetime import date

import duckdb
import pandas as pd

from statera import contracts
from statera.project import Unit


def odps(unit: Unit) -> dict:
    return unit.read("1-specify/product.odps.yaml")


def output_ports(unit: Unit) -> list[dict]:
    return odps(unit).get("outputPorts", [])


def port_contract(unit: Unit, port: str) -> dict:
    all_ = contracts.all_(unit.project)
    for p in output_ports(unit):
        if p["name"] == port:
            return all_[p["contractId"]][1]
    raise KeyError(f"{unit.name}: output port '{port}' does not exist in product.odps.yaml")


def key(contract: dict) -> list[str]:
    props = contract["schema"][0]["properties"]
    pk = sorted((p for p in props if p.get("primaryKey")), key=lambda p: p.get("primaryKeyPosition", 1))
    return [p["name"] for p in pk] or [props[0]["name"]]


def read(contract: dict, con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Reads the contract's columns from the engine, ordered by the key."""
    obj = contract["schema"][0]
    columns = ", ".join(p["name"] for p in obj["properties"])
    return con.execute(f"SELECT {columns} FROM {obj['physicalName']} ORDER BY {', '.join(key(contract))}").df()


def read_port(unit: Unit, port: str) -> pd.DataFrame:
    con = duckdb.connect(str(unit.project.warehouse), read_only=True)
    try:
        return read(port_contract(unit, port), con)
    finally:
        con.close()


def contract_owner(project, contract_id: str) -> Unit | None:
    all_ = contracts.all_(project)
    return project.unit_of(all_[contract_id][0]) if contract_id in all_ else None


def input_products(unit: Unit) -> list[Unit]:
    """INTERNAL products the unit depends on (through the ODPS input ports)."""
    seen, output = set(), []
    for port in odps(unit).get("inputPorts", []) or []:
        owner = contract_owner(unit.project, port["contractId"])
        if owner and owner.internal and owner.name not in seen and owner.name != unit.name:
            seen.add(owner.name)
            output.append(owner)
    return output


def authorized_consumers(unit: Unit) -> dict[str, str]:
    """{product or query id: purpose} declared by the internal product.

    Each item authorizes a product (`product: foo`) or a cross-cutting recurring query
    (`query: org.example.labor-stats.query.x`), always with the purpose (LGPD, art. 6º, I).
    Team authorizations (`role`) are in `authorized_roles`.
    """
    items = unit.custom_property("authorizedConsumers", []) or []
    return {c.get("product") or c.get("query"): c.get("purpose", "") for c in items if not c.get("role")}


def authorized_roles(unit: Unit) -> list[dict]:
    """Teams authorized to read the internal product directly (in the organization's environment, no download):
    `{role, purpose, until, act}`. Access belongs to the team, for one purpose and up to a date, and the
    act says who authorized it (LGPD, art. 6º, I and III; Decreto nº 10.046/2019)."""
    return [c for c in unit.custom_property("authorizedConsumers", []) or [] if c.get("role")]


def current_authorization(unit: Unit, role: str, today: date | None = None) -> dict | None:
    today = today or date.today()
    for a in authorized_roles(unit):
        if a["role"] == role and a.get("until") and date.fromisoformat(str(a["until"])) >= today:
            return a
    return None


def products_read_by_query(project, bases: list[str]) -> list[Unit]:
    """INTERNAL products that own the tables a recurring query reads."""
    by_table = contracts.by_table(project)
    all_ = contracts.all_(project)
    output = []
    for base in bases:
        contract = by_table.get(base)
        owner = project.unit_of(all_[contract["id"]][0]) if contract else None
        if owner and owner.internal and owner not in output:
            output.append(owner)
    return output
