"""The Copier template generates a product born at level 1 (catalogued), except for what only the team knows."""

from copier import run_copy

from statera import conformance, validation
from statera.project import PACKAGE, SKELETON, current, read_yaml


def test_new_product_only_asks_where_it_is_accessed(minimal_project):
    destination = minimal_project.root / "products" / "monthly-rais"
    data = {
        "product": "monthly-rais",
        "title": "Monthly RAIS",
        "namespace": "org.example",
        "curator": "team-x",
        "technical_owner": "team-x",
        "data_protection_officer": "team-x",
    }
    run_copy(str(PACKAGE / "templates" / "product"), str(destination), data=data, defaults=True, quiet=True)
    assert (destination / ".copier-answers.yml").exists()  # allows `copier update` when the template evolves
    odps = read_yaml(destination / "1-specify" / "product.odps.yaml")
    assert list(validation._validator("odps").iter_errors(odps)) == []
    for file, schema in (("classification.yaml", "classification"), ("owners.yaml", "owners")):
        assert list(validation._validator(schema).iter_errors(read_yaml(destination / "1-specify" / file))) == []
    unit = current().product("monthly-rais")
    outstanding = conformance.REQUIREMENTS[1](unit, {"contracts": {}})
    assert len(outstanding) == 1 and "distributions" in outstanding[0]  # level 1 requires where the public accesses it
    # complete skeleton: what does not exist yet declares why, instead of being missing
    for rel in SKELETON:
        assert (destination / rel).exists(), rel
        assert unit.not_applicable(rel).startswith("to be de"), rel


def test_internal_product_asks_for_authorized_consumers(minimal_project):
    destination = minimal_project.root / "products" / "employment-links"
    data = {"product": "employment-links", "title": "Employment links", "namespace": "org.example",
            "alignment": "aggregate", "curator": "team-x", "technical_owner": "team-x",
            "data_protection_officer": "team-x"}  # fmt: skip
    run_copy(str(PACKAGE / "templates" / "product"), str(destination), data=data, defaults=True, quiet=True)
    odps = read_yaml(destination / "1-specify" / "product.odps.yaml")
    assert list(validation._validator("odps").iter_errors(odps)) == []
    unit = current().product("employment-links")
    assert (unit.alignment, unit.internal) == ("aggregate", True)
    outstanding = conformance.INTERNAL_REQUIREMENTS[1](unit, {"contracts": {}})
    assert any("authorizedConsumers" in p for p in outstanding)  # level 1 requires who may read it
    assert "not disseminated" in unit.not_applicable("7-disseminate/series.yaml")
    assert not (destination / "1-specify/calendar.yaml").exists()  # generated from the consumers (sync)
