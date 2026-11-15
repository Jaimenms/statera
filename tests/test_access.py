"""How the public accesses a product: declared distributions and visualizations (dashboards and charts)."""

from statera import access
from statera.project import current


def _panel(**meta) -> dict:
    base = {"product": "sample", "tool": "Power BI", "reads": "published-edition", "updated": "monthly",
            "sharing": "broad", "visualization": "dashboard"}  # fmt: skip
    return {"name": "sample_dashboard", "url": "https://example.org/dashboard", "meta": {**base, **meta}}


def test_complete_dashboard_has_no_problems(minimal_project):
    assert access.visualization_problems(current(), _panel()) == []


def test_public_dashboard_reads_the_published_edition(minimal_project):
    problems = access.visualization_problems(current(), _panel(reads="current"))
    assert any("must read the published edition" in p for p in problems)


def test_internal_dashboard_may_read_the_engine(minimal_project):
    assert access.visualization_problems(current(), _panel(reads="current", sharing="restricted")) == []


def test_dashboard_without_url_or_tool(minimal_project):
    e = _panel(tool=None)
    e["url"] = None
    problems = access.visualization_problems(current(), e)
    assert any("url" in p for p in problems) and any("tool" in p for p in problems)


def test_chart_requires_alt_text_and_port_with_the_numbers(minimal_project):
    charts = [{"title": "Net change by UF", "port": "nonexistent"}]
    problems = access.visualization_problems(current(), _panel(visualization="chart", charts=charts))
    assert any("alt_text" in p for p in problems)
    assert any("port 'nonexistent'" in p for p in problems)


def test_dissemination_product_without_access_method(minimal_project):
    problems = access.distribution_problems(current().product("sample"))
    assert any("no means of access" in p for p in problems)


def test_visualization_with_author_without_name(minimal_project):
    problems = access.visualization_problems(current(), _panel(authors=[{"unit": "Advisory Office"}]))
    assert any("author without a name" in p for p in problems)


def test_recipe_records_authorship(tmp_path):
    from statera.consume import recipes

    file = tmp_path / "01-example.py"
    file.write_text("# Recipe: Example\n# Layers: gold\n# Classification: broad\n# Authors: Maria (Advisory Office)\n")
    assert recipes._header(file)["authors"] == "Maria (Advisory Office)"
