"""Authorization of teams (roles) to internal products: purpose, legal act and expiry date."""

from datetime import date

import pytest

from statera import ports, validation
from statera.consume import catalog
from statera.project import current


class _Internal:
    name = "internal"
    internal = True

    def __init__(self, items):
        self._items = items

    def custom_property(self, name, default=None):
        return self._items if name == "authorizedConsumers" else default


def test_authorization_is_valid_until_the_date():
    u = _Internal([{"role": "team-x", "purpose": "study", "until": "2026-12-31", "act": "Ordinance 1"}])
    assert ports.current_authorization(u, "team-x", today=date(2026, 12, 31))
    assert ports.current_authorization(u, "team-x", today=date(2027, 1, 1)) is None
    assert ports.authorized_consumers(u) == {}  # a role is not mistaken for a product or query


def test_validation_blocks_expired_or_incomplete_authorization(minimal_project):
    u = _Internal(
        [
            {"role": "team-x", "purpose": "study", "until": "2020-01-01", "act": "Ordinance 1"},
            {"role": "nonexistent", "until": "2099-01-01"},
        ]
    )
    problems = validation._role_problems(current(), u)
    assert any("expired on 2020-01-01" in p for p in problems)
    assert any("'nonexistent': role does not exist" in p for p in problems)
    assert any("missing 'act'" in p for p in problems) and any("missing 'purpose'" in p for p in problems)


def test_library_denies_internal_product_without_authorization(minimal_project):
    catalog.open(role="team-x")._authorize("bronze.records")  # authorized until 2099
    with pytest.raises(catalog.AccessDenied):
        catalog.open(role="other-team")._authorize("bronze.records")
    catalog.open()._authorize("bronze.records")  # no role: data team (warning only)
