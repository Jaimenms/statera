"""Lineage read at two levels: assets and products (one product, all products, impact by product)."""

import yaml

from statera import lineage
from statera.process import dbt


def _manifest() -> dict:
    """base-data (source) -> consumer (gold + exposure); a cross-cutting query reads the consumer table."""

    def node(kind, name, path, parents=(), **extra):
        return {
            "resource_type": kind, "name": name, "original_file_path": path, "description": "",
            "depends_on": {"nodes": list(parents)}, "config": {"meta": extra.pop("meta", {})}, **extra,
        }  # fmt: skip

    staging = "model.p.stg_base__records"
    gold = "model.p.consumer_table"
    return {
        "sources": {
            "source.p.base.records": {
                "resource_type": "source",
                "name": "records",
                "source_name": "base",
                "description": "",
                "original_file_path": "products/base-data/5-process/models/staging/base/_sources.yml",
            }  # fmt: skip
        },
        "nodes": {
            staging: node(
                "model",
                "stg_base__records",
                "products/base-data/5-process/models/staging/x.sql",
                ["source.p.base.records"],
                meta={"layer": "silver"},
            ),
            gold: node(
                "model",
                "consumer_table",
                "products/consumer/5-process/models/marts/consumer_table.sql",
                [staging],
                meta={"layer": "gold"},
            ),
            "analysis.p.cross_query": node("analysis", "cross_query", "queries/x/cross_query.sql", [gold]),
        },  # fmt: skip
        "exposures": {
            "exposure.p.release": {
                "name": "release",
                "label": "Monthly release",
                "type": "analysis",
                "description": "",
                "meta": {},
                "depends_on": {"nodes": [gold]},
                "original_file_path": "products/consumer/5-process/models/marts/_models.yml",
            }  # fmt: skip
        },
    }


def _owners(project):
    path = project.root / "products/consumer/1-specify/owners.yaml"
    path.write_text("scope: product\nunit: consumer\ntechnical_owner: team-x\npaths: []\n", encoding="utf-8")


def test_every_node_knows_its_product(minimal_project):
    g = lineage.build(_manifest())
    assert g.nodes["source.p.base.records"].unit == "base-data"
    assert g.nodes["model.p.consumer_table"].group == "product:consumer"
    assert g.nodes["exposure.p.release"].unit == "consumer"
    assert g.nodes["analysis.p.cross_query"].group == "queries"  # outside every product
    assert lineage.unit_edges(g) == {("base-data", "consumer")}
    assert lineage.group_order(g).index("product:base-data") < lineage.group_order(g).index("product:consumer")


def test_impact_of_a_product_is_grouped_by_product_with_owner(minimal_project):
    _owners(minimal_project)
    g = lineage.build(_manifest())
    report = lineage.impact_report(g, "base-data")
    consumer = next(r for r in report if r["group"] == "product:consumer")
    assert consumer["owner"]["git_team"] == "@org/team-x"
    assert {label for _, _, label in consumer["assets"]} == {"consumer_table", "Monthly release"}
    assert any(r["group"] == "queries" for r in report)


def test_one_product_view_keeps_upstream_and_downstream(minimal_project):
    g = lineage.build(_manifest())
    view = lineage.neighborhood(g, "consumer")
    assert "source.p.base.records" in view.nodes and "analysis.p.cross_query" in view.nodes
    mermaid = lineage.to_mermaid(view, focus="consumer")
    assert 'subgraph n_g_product_consumer["consumer' in mermaid
    assert "stroke-width:4px" in mermaid and "stroke-dasharray" in mermaid  # focus vs. the other products


def test_dbt_project_tags_and_colors_each_product(minimal_project):
    model = minimal_project.root / "products/consumer/5-process/models/marts/consumer/consumer_table.sql"
    model.parent.mkdir(parents=True)
    model.write_text("select 1 as x", encoding="utf-8")
    text = dbt.generate_project(minimal_project)
    config = yaml.safe_load(text)["models"]["example_org"]["marts"]["consumer"]["consumer_table"]
    assert config["+tags"] == ["product:consumer", "alignment:consumer"]
    assert config["+docs"]["node_color"] == minimal_project.product_colors()["consumer"]
    assert len(set(minimal_project.product_colors().values())) == len(minimal_project.products())


def test_a_query_is_grouped_with_the_product_its_axis_names(minimal_project):
    folder = minimal_project.root / "queries/theme/by-consumer"
    folder.mkdir(parents=True)
    (folder / "query.yaml").write_text("product: consumer\n", encoding="utf-8")
    manifest = _manifest()
    manifest["nodes"]["analysis.p.cross_query"]["original_file_path"] = "queries/theme/by-consumer/q.sql"
    g = lineage.build(manifest)
    assert g.nodes["analysis.p.cross_query"].group == "product:consumer"


def test_queries_inside_a_product_fail_validation(minimal_project):
    from statera import validation

    folder = minimal_project.root / "products/sample/queries/old"
    folder.mkdir(parents=True)
    (folder / "query.yaml").write_text("theme: work\n", encoding="utf-8")
    [finding] = validation.check_query_location(minimal_project)
    assert not finding.ok and "move it to queries/work/old/" in finding.message
