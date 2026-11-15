"""`statera synthetic`: SyntheticSource classes run in dependency order; faults only where declared."""

import pytest

from statera.extend import synthetic

SOURCE = '''
from statera.extend import SyntheticSource

CALLS = []


class {name}(SyntheticSource):
    """Stands in for a system of {product}."""

    sources = ("bronze.records",)
    faults = frozenset({faults})

    def generate(self, ctx, fault=None):
        CALLS.append((ctx.product, fault))
        return {{"records": 3}}
'''


def _source(project, product: str, name: str, faults: str = "") -> None:
    path = project.root / f"products/{product}/3-build/synthetic.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SOURCE.format(name=name, product=product, faults=faults), encoding="utf-8")


def test_sources_run_in_dependency_order_and_faults_go_where_declared(minimal_project):
    _source(minimal_project, "consumer", "ConsumerSystem")  # reads base-data: runs after it
    _source(minimal_project, "base-data", "BaseSystem", faults='{"distribution"}')
    generated = synthetic.run(minimal_project, fault="distribution", log=lambda _: None)
    assert [(g.product, g.source, g.fault) for g in generated] == [
        ("base-data", "BaseSystem", "distribution"),
        ("consumer", "ConsumerSystem", None),  # it does not declare the fault
    ]
    assert generated[0].counts == {"records": 3}


def test_an_undeclared_fault_is_refused(minimal_project):
    _source(minimal_project, "base-data", "BaseSystem", faults='{"distribution"}')
    with pytest.raises(synthetic.SyntheticError, match="'late'.*declared: distribution"):
        synthetic.run(minimal_project, fault="late")


def test_one_product(minimal_project):
    _source(minimal_project, "base-data", "BaseSystem")
    _source(minimal_project, "consumer", "ConsumerSystem")
    assert [g.product for g in synthetic.run(minimal_project, product="consumer", log=lambda _: None)] == ["consumer"]


def test_without_classes_the_legacy_hook_runs_with_a_warning(minimal_project):
    hook = minimal_project.root / "products/base-data/3-build/legacy.py"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("def make(project, failure=None, references=False):\n    return {'x': 1}\n", encoding="utf-8")
    toml = minimal_project.root / "statera.toml"
    toml.write_text(
        toml.read_text(encoding="utf-8") + '\n[demo]\nsynthetic = "products/base-data/3-build/legacy.py:make"\n',
        encoding="utf-8",
    )
    from statera.project import Project

    with pytest.warns(DeprecationWarning, match="deprecated since statera 0.6"):
        generated = synthetic.run(Project(minimal_project.root))  # re-read: statera.toml changed
    assert generated[0].counts == {"x": 1}


def test_no_sources_and_no_hook_is_an_error(minimal_project):
    with pytest.raises(synthetic.SyntheticError, match="no product has synthetic sources"):
        synthetic.run(minimal_project)
