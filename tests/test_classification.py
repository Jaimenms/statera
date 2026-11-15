"""Four-dimension classification and the open-publication gate."""

from statera import classification


def _contract(table: dict, columns: list[dict]) -> dict:
    return {"customProperties": [{"property": "classification", "value": table}], "schema": [{"properties": columns}]}


BROAD = {"sharing": "broad", "nature": "aggregated", "secrecy": "none"}


def test_broad_aggregated_table_can_be_published():
    c = _contract(BROAD, [{"name": "net_change", "classification": "broad"}])
    assert classification.consistency(c) == []
    assert classification.publication_blockers(c, ["net_change"], applied_control=False) == []


def test_personal_column_requires_personal_nature():
    c = _contract(BROAD, [{"name": "cpf", "classification": "broad", "tags": ["personal-data"]}])
    assert any("nature" in e for e in classification.consistency(c))
    assert any("personal data" in m for m in classification.publication_blockers(c, ["cpf"], False))


def test_statistical_confidentiality_requires_applied_control():
    control = {
        "method": "cell-suppression",
        "column": "n",
        "minimum": 3,
        "suppressedColumns": ["x"],
        "unpublishedColumns": ["n"],
    }
    c = _contract(
        {**BROAD, "secrecy": "statistical", "disclosureControl": control},
        [{"name": "x", "classification": "broad"}, {"name": "n", "classification": "restricted"}],
    )
    assert classification.consistency(c) == []  # an unpublished restricted column is allowed
    assert classification.publication_blockers(c, ["x"], applied_control=False)
    assert classification.publication_blockers(c, ["x"], applied_control=True) == []


def test_restricted_never_goes_to_open_publication():
    c = _contract({"sharing": "restricted", "nature": "deidentified", "secrecy": "none"}, [])
    assert any("sharing" in m for m in classification.publication_blockers(c, [], True))


def test_approval_must_match_the_contract():
    c = _contract(BROAD, [])
    assert classification.approval_matches(c, {**BROAD}) == []
    assert classification.approval_matches(c, {**BROAD, "nature": "personal"})
    assert classification.approval_matches(c, None) == ["no approval in classification.yaml"]
