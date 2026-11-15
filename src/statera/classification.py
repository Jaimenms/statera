"""Information classification in four dimensions (specification v0, `03-classification.md`).

| Dimension          | Values                                                     | Source                    |
|--------------------|------------------------------------------------------------|---------------------------|
| sharing            | broad · restricted · specific                              | Decreto nº 10.046/2019    |
| nature             | aggregated · deidentified · personal · sensitive-personal  | LGPD (Lei nº 13.709/2018) |
| secrecy            | none · statistical · tax · other                           | specific legislation; LAI |
| disclosureControl  | absent; cell-suppression (aggregates) or k-anonymity       | GSBPM 6.4                 |
|                    | (deidentified microdata)                                   |                           |

Where each thing lives:
  - in the CONTRACT (statera profile): `customProperties.classification` with the table's four
    dimensions; on each column, `classification` (broad | restricted | specific) and, where
    applicable, the `personal-data` or `sensitive-personal-data` tag in `tags`;
  - in the APPROVAL (the unit's `1-specify/classification.yaml`, owner: data protection officer):
    the same first three dimensions and the legal basis. CI requires the two to match: whoever
    writes the contract does not approve its own classification alone.

Open publication rule: broad sharing; aggregated or deidentified nature (the latter only with
k-anonymity); no confidentiality, or statistical confidentiality with statistical disclosure control
applied; and no published column that is not broad or that is personal data.
"""

from __future__ import annotations

SHARING = ("broad", "restricted", "specific")
NATURE = ("aggregated", "deidentified", "personal", "sensitive-personal")
CONFIDENTIALITY = ("none", "statistical", "tax", "other")
PERSONAL_TAGS = ("personal-data", "sensitive-personal-data")
DIMENSIONS = ("sharing", "nature", "secrecy")


def of_table(contract: dict) -> dict:
    from statera.contracts import custom_property_of

    return custom_property_of(contract, "classification") or {}


def required_control(contract: dict) -> dict | None:
    return of_table(contract).get("disclosureControl")


def personal_column(prop: dict) -> str | None:
    tags = set(prop.get("tags") or [])
    if "sensitive-personal-data" in tags:
        return "sensitive-personal"
    if "personal-data" in tags:
        return "personal"
    return None


def summary(classif: dict) -> str:
    parts = [classif.get(d, "?") for d in DIMENSIONS]
    if classif.get("disclosureControl"):
        parts.append("with disclosure control")
    return " · ".join(parts)


def consistency(contract: dict) -> list[str]:
    """Consistency errors between the table's classification and the columns' classification."""
    errors = []
    table = of_table(contract)
    for dim, values in (("sharing", SHARING), ("nature", NATURE), ("secrecy", CONFIDENTIALITY)):
        if table.get(dim) not in values:
            errors.append(f"{dim} '{table.get(dim)}' not in {list(values)}")
    unpublished = set((table.get("disclosureControl") or {}).get("unpublishedColumns", []))
    personal = []
    for prop in contract["schema"][0].get("properties", []):
        level = prop.get("classification")
        if level not in SHARING:
            errors.append(f"column {prop['name']}: classification '{level}' not in {list(SHARING)}")
        if table.get("sharing") == "broad" and level != "broad" and prop["name"] not in unpublished:
            errors.append(f"column {prop['name']} is '{level}' in a broad-sharing table")
        if nature := personal_column(prop):
            personal.append(nature)
    control = table.get("disclosureControl") or {}
    if control.get("method") == "k-anonymity":
        columns = {p["name"] for p in contract["schema"][0].get("properties", [])}
        cited = [*control.get("quasiIdentifiers", []), *control.get("suppressibleColumns", [])]
        errors += [
            f"k-anonymity: column '{c}' does not exist in the contract"
            for c in dict.fromkeys(cited)
            if c not in columns
        ]
        errors += [
            f"k-anonymity: suppressible column '{c}' is not a quasi-identifier"
            for c in control.get("suppressibleColumns", [])
            if c not in control.get("quasiIdentifiers", [])
        ]
    if "sensitive-personal" in personal and table.get("nature") != "sensitive-personal":
        errors.append("a column holds sensitive personal data; the table nature should be sensitive-personal")
    elif personal and table.get("nature") not in ("personal", "sensitive-personal"):
        errors.append("a column holds personal data; the table nature should be personal or sensitive-personal")
    return errors


def publication_blockers(contract: dict, published_columns: list[str], applied_control: bool) -> list[str]:
    """Reasons why the table cannot go to open publication (empty list = it can)."""
    reasons = []
    t = of_table(contract)
    if t.get("sharing") != "broad":
        reasons.append(f"sharing '{t.get('sharing')}' (only 'broad' is publishable)")
    if t.get("nature") not in ("aggregated", "deidentified"):
        reasons.append(f"nature '{t.get('nature')}' (only aggregated or deidentified)")
    confidentiality = t.get("secrecy")
    if confidentiality not in ("none", "statistical"):
        reasons.append(f"secrecy '{confidentiality}' prevents open publication")
    requires_control = bool(t.get("disclosureControl")) or confidentiality == "statistical"
    if requires_control and not applied_control:
        reasons.append("requires disclosure control and it was not applied")
    if confidentiality == "statistical" and not t.get("disclosureControl"):
        reasons.append("statistical confidentiality without a declared disclosure control method")
    if t.get("nature") == "deidentified" and (t.get("disclosureControl") or {}).get("method") != "k-anonymity":
        reasons.append("deidentified microdata are released only with k-anonymity control (re-identification risk)")
    for prop in contract["schema"][0].get("properties", []):
        if prop["name"] not in published_columns:
            continue
        if prop.get("classification") != "broad":
            reasons.append(f"column {prop['name']} is '{prop.get('classification')}' and would be published")
        if personal_column(prop):
            reasons.append(f"column {prop['name']} is personal data and would be published")
    return reasons


def approval_matches(contract: dict, approved: dict | None) -> list[str]:
    """Compares the contract's classification with the one approved in classification.yaml."""
    if approved is None:
        return ["no approval in classification.yaml"]
    t = of_table(contract)
    errors = [
        f"{dim}: contract says '{t.get(dim)}', classification.yaml says '{approved.get(dim)}'"
        for dim in DIMENSIONS
        if t.get(dim) != approved.get(dim)
    ]
    method = (t.get("disclosureControl") or {}).get("method")
    if method != approved.get("disclosure_control"):
        errors.append(
            f"disclosure control: contract says '{method}', "
            f"classification.yaml says '{approved.get('disclosure_control')}'"
        )
    return errors
