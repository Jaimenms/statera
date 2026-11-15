"""Data repository tests (beyond the framework's validation)."""

from statera.project import current


def test_project_loads():
    assert current().name
