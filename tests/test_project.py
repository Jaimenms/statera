"""Project discovery: units, namespace, product mesh and data folders."""

from statera import project


def test_discovers_units_and_namespace(minimal_project):
    p = minimal_project
    assert [(u.kind, u.name) for u in p.units] == [
        ("product", "base-data"), ("product", "consumer"), ("product", "sample"),
    ]  # fmt: skip
    assert p.product("sample").urn == "org.example.sample"
    assert p.processes()[-1] == p.root / "products/sample/2-design/processes/to-be.yaml"


def test_product_alignment_and_visibility(minimal_project):
    from statera import ports

    base, consumer = minimal_project.product("base-data"), minimal_project.product("consumer")
    assert (base.alignment, base.internal) == ("source", True)
    assert (consumer.alignment, consumer.internal) == ("consumer", False)
    assert [u.name for u in ports.input_products(consumer)] == ["base-data"]
    assert ports.authorized_consumers(base) == {"consumer": "test"}


def test_data_folders_by_phase_and_isolation_in_tests(minimal_project, tmp_path, monkeypatch):
    u = minimal_project.product("sample")
    assert u.data(7) == u.folder / "7-disseminate" / "data"
    monkeypatch.setenv("STATERA_DATA", str(tmp_path / "isolated"))
    isolated = project.current()
    assert (
        isolated.product("sample").data(7)
        == (tmp_path / "isolated" / "products" / "sample" / "7-disseminate").resolve()
    )
    assert isolated.warehouse.parent == (tmp_path / "isolated" / "engine").resolve()
