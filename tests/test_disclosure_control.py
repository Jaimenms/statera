"""Statistical disclosure control for microdata: k-anonymity with local suppression, and the gate rule."""

import pandas as pd

from statera import classification
from statera.analyse.disclosure_control import apply

RULE = {
    "method": "k-anonymity",
    "k": 2,
    "quasiIdentifiers": ["municipality", "age_group"],
    "suppressibleColumns": ["municipality", "age_group"],
}


def test_rare_combination_loses_municipality_and_becomes_anonymous():
    df = pd.DataFrame(
        {
            "municipality": ["A", "A", "B", "C"],
            "age_group": ["18-24", "18-24", "25-29", "25-29"],
            "net_change": [1, 1, 1, -1],
        }
    )
    r = apply(df, RULE)
    # B/25-29 and C/25-29 were unique; without the municipality, they become two 25-29 records: k = 2
    assert r.suppressed_cells == 2 and r.removed_rows == 0
    assert r.data["municipality"].isna().sum() == 2
    assert r.data.groupby(["age_group"], dropna=False).size().min() >= 2


def test_what_remains_unique_is_removed():
    df = pd.DataFrame(
        {"municipality": ["A", "A", "B"], "age_group": ["18-24", "18-24", "60+"], "net_change": [1, 1, 1]}
    )
    r = apply(df, RULE)
    assert r.removed_rows == 1 and len(r.data) == 2


def _contract(control: dict | None) -> dict:
    classif = {"sharing": "broad", "nature": "deidentified", "secrecy": "statistical"}
    if control:
        classif["disclosureControl"] = control
    columns = [{"name": n, "classification": "broad"} for n in ("municipality", "age_group", "net_change")]
    return {"customProperties": [{"property": "classification", "value": classif}], "schema": [{"properties": columns}]}


def test_deidentified_microdata_require_k_anonymity():
    suppression = {
        "method": "cell-suppression",
        "column": "net_change",
        "minimum": 3,
        "suppressedColumns": ["net_change"],
    }
    reasons = classification.publication_blockers(_contract(suppression), ["municipality", "net_change"], True)
    assert any("k-anonymity" in m for m in reasons)
    assert classification.publication_blockers(_contract(RULE), ["municipality", "net_change"], True) == []


def test_k_anonymity_with_missing_column():
    rule = {**RULE, "quasiIdentifiers": ["municipality", "occupation"], "suppressibleColumns": ["municipality"]}
    assert any("column 'occupation' does not exist" in e for e in classification.consistency(_contract(rule)))
