"""Statistical disclosure control and classification gate (GSBPM 6.4 — Apply disclosure control).

Before any file is generated (phase 7), the candidate tables go through here:
  - the statistical disclosure control declared in `disclosureControl` in the contract's classification:
      * `cell-suppression` (aggregated tables): suppresses the measures where the count falls below the minimum;
      * `k-anonymity` (deidentified microdata): every combination of quasi-identifiers appears in at
        least k records; where it does not, blanks the values of the suppressible columns, in the
        declared order (local suppression, like sdcMicro's `localSuppression`), and removes whatever
        is still below k;
  - open publication gate (Decreto nº 10.046/2019, LGPD, LAI): the contract's classification must
    match the one approved in classification.yaml and allow open publication.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from statera import classification


class ClassificationError(RuntimeError):
    """The publication would include a non-publishable asset."""


@dataclass
class Table:
    port: str
    description: str
    sheet: str
    contract_id: str
    contract: dict
    data: pd.DataFrame
    unpublished_columns: list[str] = field(default_factory=list)
    suppressed_columns: list[str] = field(default_factory=list)
    suppressed_cells: int = 0
    removed_rows: int = 0
    applied_control: bool = False
    in_spreadsheet: bool = True  # microdata stay out of the xlsx (port with inSpreadsheet: false)

    @property
    def obj(self) -> dict:
        return self.contract["schema"][0]

    @property
    def physical_table(self) -> str:
        return self.obj["physicalName"]

    @property
    def control(self) -> dict | None:
        return classification.required_control(self.contract)


@dataclass
class Result:
    data: pd.DataFrame
    suppressed_cells: int = 0
    removed_rows: int = 0
    suppressed_columns: list[str] = field(default_factory=list)


def _cell_suppression(df: pd.DataFrame, rule: dict) -> Result:
    suppress = df[rule["column"]] < rule["minimum"]
    for col in rule["suppressedColumns"]:
        df[col] = df[col].astype("Int64").mask(suppress)
    df["suppressed"] = suppress
    return Result(df, int(suppress.sum()), 0, list(rule["suppressedColumns"]))


def _k_anonymity(df: pd.DataFrame, rule: dict) -> Result:
    qi, k = list(rule["quasiIdentifiers"]), int(rule["k"])
    df = df.copy()

    def at_risk(d: pd.DataFrame) -> pd.Series:
        if d.empty:
            return pd.Series(False, index=d.index)
        size = d[qi].astype(object).fillna("∅").groupby(qi, dropna=False)[qi[0]].transform("size")
        return size < k

    suppressed = 0
    for col in rule["suppressibleColumns"]:
        risk = at_risk(df)
        if not risk.any():
            break
        suppressed += int((risk & df[col].notna()).sum())
        df[col] = df[col].astype(object).where(~risk, None)
    risk = at_risk(df)
    return Result(df[~risk].reset_index(drop=True), suppressed, int(risk.sum()), list(rule["suppressibleColumns"]))


METHODS = {"cell-suppression": _cell_suppression, "k-anonymity": _k_anonymity}


def apply(df: pd.DataFrame, rule: dict | None) -> Result:
    """Applies the declared control (publication and queries use the same rule)."""
    if not rule:
        return Result(df)
    if rule["method"] not in METHODS:
        raise ClassificationError(f"control method '{rule['method']}' not supported")
    result = METHODS[rule["method"]](df, rule)
    result.data = result.data.drop(columns=rule.get("unpublishedColumns", []))
    return result


def apply_disclosure_control(table: Table) -> None:
    """Statistical disclosure control (GSBPM 6.4) according to the asset's contract."""
    rule = table.control
    if not rule:
        return
    try:
        r = apply(table.data, rule)
    except ClassificationError as error:
        raise ClassificationError(f"{table.contract_id}: {error}") from error
    table.data = r.data
    table.suppressed_cells, table.removed_rows = r.suppressed_cells, r.removed_rows
    table.suppressed_columns = r.suppressed_columns
    table.unpublished_columns = list(rule.get("unpublishedColumns", []))
    table.applied_control = True


def check_publication(tables: list[Table], approval: dict) -> None:
    """Gate: only what is approved in classification.yaml and openly publishable gets out."""
    approved_assets = {a["contract"]: a for a in approval.get("assets", [])}
    errors = []
    for t in tables:
        errors += [
            f"{t.contract_id}: {e}"
            for e in classification.approval_matches(t.contract, approved_assets.get(t.contract_id))
        ]
        columns = [c for c in t.data.columns if c != "suppressed"]
        reasons = classification.publication_blockers(t.contract, columns, t.applied_control)
        errors += [f"{t.contract_id}: {m}" for m in reasons]
    if errors:
        raise ClassificationError("Publication blocked:\n  - " + "\n  - ".join(errors))
