"""The catalog page (`statera docs --page`): what it gathers from the repository and how it is written."""

import json
import re
import textwrap
from pathlib import Path

import pytest

from statera import cli, generated, lineage, page, validation
from statera.consume import queries
from statera.project import Project

GOLD = """
kind: DataContract
id: gold-sample-by-uf
name: Sample by UF
version: 1.0.0
description: {purpose: Records by UF and reference month, usage: Monthly release, limitations: none}
customProperties:
  - {property: layer, value: gold}
  - {property: dbtModel, value: sample_by_uf}
  - property: classification
    value: {sharing: broad, nature: aggregated, secrecy: none}
schema:
  - name: sample_by_uf
    physicalName: gold.sample_by_uf
    dataGranularityDescription: One row per reference month and UF
    properties:
      - {name: period, logicalType: string, primaryKey: true}
      - {name: uf, logicalType: string, primaryKey: true, description: UF abbreviation}
      - {name: records, logicalType: integer, description: Number of records}
      - {name: cut_date, logicalType: date}
"""

QUERY = """
id: org.example.query.by-uf
title: Series by UF
description: Monthly records of one UF </script><script>alert(1)</script>
theme: test
product: sample
bases: [gold.sample_by_uf]
frequency: on-demand
purpose: study
parameters:
  - {name: uf, type: uf, required: true, description: UF abbreviation}
  - {name: since, type: period}
rules: [BR-SAMPLE-001]
limitations: none
output_classification: {sharing: broad, nature: aggregated, secrecy: none}
owner: team-x
status: cataloged
version: 0.1.0
"""

SQL = """
-- Series by UF.
select period, uf, records
from {{ ref('sample_by_uf') }}
where uf = '{{ var("uf") }}' and period >= '{{ var("since", "2026-01") }}' and records > {{ var("floor", 0) }}
order by period
"""

TEST = """
cases:
  - id: one_uf
    description: Only the requested UF.
    parameters: {uf: SP}
    inputs:
      sample_by_uf:
        - {period: "2026-01", uf: SP, records: 10}
        - {period: "2026-01", uf: RJ, records: 4}
    expected:
      - {period: "2026-01", uf: SP, records: 10}
"""

METRICS = """
metrics:
  - name: total
    label: Total records
    description: Sum of the records
    type: simple
    aggregation: sum
    expression: records
    time: period
    sources:
      - {table: gold.sample_by_uf, dimensions: [uf]}
"""

RULE = """
# BR-SAMPLE-001 — A record counts once

| Field | Value |
|---|---|
| Layer | gold |
| Implementation | `sample_by_uf` (dbt) |

## Definition

A record counts **once**, in the month of its event. See [the note](../note.md) and <b>nothing else</b>.
"""

CASES = """
rule: BR-SAMPLE-001
model: sample_by_uf
cases:
  - id: counted_once
    description: Two rows of the same record count as one.
    inputs:
      records:
        - {id: a, uf: SP}
        - {id: a, uf: SP}
    expected:
      - {uf: SP, records: 1}
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")


@pytest.fixture
def repository(minimal_project):
    """The minimal project with a gold table, its model and rule, a query with a test, a metric and a recipe."""
    root = minimal_project.root
    _write(root / "products/sample/5-process/contracts/sample_by_uf.odcs.yaml", GOLD)
    _write(root / "products/sample/5-process/models/marts/sample/sample_by_uf.sql", "-- Records by UF.\nselect 1")
    _write(root / "products/sample/5-process/models/marts/sample/sample_by_uf.sps", "* Legacy syntax.\nGET FILE=x.")
    _write(root / "products/sample/5-process/rules/BR-SAMPLE-001-once/rule.md", RULE)
    _write(root / "products/sample/5-process/rules/BR-SAMPLE-001-once/cases.yaml", CASES)
    _write(root / "products/sample/5-process/metrics.yaml", METRICS)
    _write(root / "queries/test/by-uf/query.yaml", QUERY)
    _write(root / "queries/test/by-uf/by_uf.sql", SQL)
    _write(root / "queries/test/by-uf/test.yaml", TEST)
    _write(root / "recipes/series.sql", "-- Recipe: A series\n-- Layers: gold\n-- Classification: broad\nselect 1")
    return minimal_project


def test_the_page_gathers_what_the_repository_declares(repository):
    data = page.build(repository, parser=cli.parser(), graph=None)

    table = next(t for t in data["tables"] if t["name"] == "gold.sample_by_uf")
    assert table["unit"] == "sample" and table["grain"] == "One row per reference month and UF"
    assert [c["name"] for c in table["columns"] if c["key"]] == ["period", "uf"]
    assert any("a row is a group" in note for note in table["notes"])  # the notes the copilot's card has

    query = data["queries"][0]
    assert query["slug"] == "by-uf" and query["bases"] == ["gold.sample_by_uf"]
    assert [(p["name"], p["required"], p["sql_default"]) for p in query["parameters"]] == [
        ("uf", True, None),
        ("since", False, "2026-01"),
    ]
    assert [c["name"] for c in query["columns"]] == ["period", "uf", "records"]
    assert query["columns"][2]["type"] == "integer"  # from the contract of the table it reads
    assert query["tests"][0]["parameters"] == {"uf": "SP"}

    assert data["metrics"][0]["sources"] == [{"table": "gold.sample_by_uf", "dimensions": ["uf"]}]
    rule = data["rules"][0]
    assert (rule["id"], rule["title"], rule["model"]) == ("BR-SAMPLE-001", "A record counts once", "sample_by_uf")
    assert rule["summary"].startswith("A record counts once, in the month of its event.")
    assert rule["cases"][0]["expected"] == [{"uf": "SP", "records": 1}]
    code = {c["name"]: c for c in data["code"]}
    assert (
        code["sample_by_uf.sql"]["table"] == "gold.sample_by_uf"
        and code["sample_by_uf.sql"]["about"] == "Records by UF."
    )
    assert code["sample_by_uf.sps"]["language"] == "spss"  # the legacy script of the same model
    assert data["recipes"][0]["title"] == "A series"

    sample = next(p for p in data["products"] if p["name"] == "sample")
    assert sample["calendar"]["rows"] == [{"edition": "2026-01", "cut_date": "2026-02-20", "release": "2026-02-27"}]
    assert sample["calendar"]["next"]["edition"] == "2026-01"
    internal = next(p for p in data["products"] if p["name"] == "base-data")
    assert internal["internal"] and {a["who"] for a in internal["authorized"]} == {"consumer", "team-x"}

    assert {c["name"] for c in data["commands"]} >= {"query", "metric", "status", "doctor"}  # only what reads
    assert "run" not in {c["name"] for c in data["commands"]}
    assert data["lineage"] == {"svg": None, "missing": None}

    _write(repository.root / "docs/lineage.svg", "<!-- generated -->\n<svg>as statera docs left it</svg>")
    assert page.build(repository, graph=None)["lineage"]["svg"] == "<svg>as statera docs left it</svg>"


def test_a_query_is_filled_as_the_framework_fills_it(repository):
    """The SQL the page fills in the browser is the one `statera query show` prints: the same text, with
    a marked place where each declared parameter goes."""
    written = page.build(repository, graph=None)["queries"][0]["written"]
    assert f"uf = '{page.OPEN}uf{page.CLOSE}'" in written and f">= '{page.OPEN}since{page.CLOSE}'" in written
    assert "records > 0" in written  # an undeclared var() is its own default, as in the framework

    filled = written.replace(f"{page.OPEN}uf{page.CLOSE}", "RJ").replace(f"{page.OPEN}since{page.CLOSE}", "2026-03")
    shown = queries.compiled_sql("by-uf", {"uf": "RJ", "since": "2026-03"}, repository)
    assert shown == f"-- org.example.query.by-uf 0.1.0 · parameters: uf=RJ, since=2026-03\n{filled}\n"


@pytest.mark.parametrize(
    "sql, plain",
    [
        ("select * from {{ ref('t') }} where uf = '{{ var(\"uf\", \"SP\") }}'", True),
        ("select 1 {# a note #} from {{ ref('t') }}", True),
        ("select * from {{ ref('t') }} {% if var('uf') %} where uf = '{{ var('uf') }}' {% endif %}", False),
        ("select * from {{ ref('t') }} limit {{ var('n') | int }}", False),
        ("select * from {{ ref(var('table')) }}", False),
        ("select {{ columns }} from {{ ref('t') }}", False),
        ("select * from {{ ref('t') ", False),
    ],
)
def test_only_sql_that_is_plain_ref_and_var_is_filled_in_the_browser(sql, plain):
    assert page.fillable(sql) is plain


def test_a_query_that_computes_its_sql_is_shown_as_written(repository):
    sql = "select 1 from {{ ref('sample_by_uf') }} {% if var('uf') %} where uf = '{{ var('uf') }}' {% endif %}"
    (repository.root / "queries/test/by-uf/by_uf.sql").write_text(sql, encoding="utf-8")
    query = page.build(repository, graph=None)["queries"][0]
    assert query["written"] is None and query["formatted"] is None and query["template"] == sql


def test_a_ref_to_a_model_that_does_not_exist_is_not_filled(repository):
    (repository.root / "queries/test/by-uf/by_uf.sql").write_text("select 1 from {{ ref('gone') }}", encoding="utf-8")
    assert page.build(repository, graph=None)["queries"][0]["written"] is None


def test_the_page_validates_by_the_types_of_the_framework(repository):
    """The page translates Python's `\\w` (letters of any language) for the browser, which only works
    inside a character class: a validator that used it elsewhere would validate differently there."""
    assert page.build(repository, graph=None)["validators"] == {k: v.pattern for k, v in queries.VALIDATORS.items()}
    for pattern in (v.pattern for v in queries.VALIDATORS.values()):
        assert all(
            m.start() > pattern.rfind("[", 0, m.start()) > pattern.rfind("]", 0, m.start())
            for m in re.finditer(r"\\w", pattern)
        )


def test_markdown_of_a_rule():
    html = page.markdown(RULE.split("\n", 2)[2])
    assert "<table><tr><th>Field</th><th>Value</th></tr><tr><td>Layer</td><td>gold</td></tr>" in html
    assert "<code>sample_by_uf</code> (dbt)" in html
    assert "<h3>Definition</h3>" in html  # one level down: the page's title is above it
    assert "A record counts <b>once</b>" in html
    assert "See the note and" in html  # a link to a file leads nowhere from the page: its text stays
    assert "&lt;b&gt;nothing else&lt;/b&gt;" in html  # HTML in the text is text
    assert page.markdown(
        "- one\n- two `x<y`\n\n1. first\n\n```\na < b\n```\n\n> quoted\n\n[site](https://example.org)"
    ) == (
        "<ul><li>one</li><li>two <code>x&lt;y</code></li></ul>\n<ol><li>first</li></ol>\n<pre>a &lt; b</pre>\n"
        '<blockquote>quoted</blockquote>\n<p><a href="https://example.org" rel="noreferrer">site</a></p>'
    )


def test_the_page_is_one_file_that_fetches_nothing(repository):
    output, data = page.write(repository, parser=cli.parser(), graph=None)
    assert output == repository.root / "target" / "catalog.html"
    text = output.read_text(encoding="utf-8")
    assert text.startswith("<!doctype html>\n<!--\n  " + generated.MARKER)

    # what the repository says travels as data, and cannot end the block it travels in
    block = re.search(r'<script type="application/json" id="data">(.*?)</script>', text, flags=re.S).group(1)
    assert "<" not in block and json.loads(block) == {**json.loads(json.dumps(data, default=str)), "theme": {}}
    assert "alert(1)" in json.loads(block)["queries"][0]["description"]
    assert text.count("<script") == 3 and "<script>alert(1)" not in text  # why it failed, the data, the page

    # nothing comes from outside the file: no script, style, font or image by address, and no request
    page_only = text.replace(block, "")
    assert not re.search(r"""(src|href)\s*=\s*["']?(https?:)?//""", page_only)
    assert not re.search(r"<link\b|@import|url\(|fetch\(|XMLHttpRequest|WebSocket|sendBeacon|<iframe", page_only)


def test_the_template_opened_as_it_is_says_what_it_is():
    """`page.html` inside the framework has no catalog in it. Opened by itself it used to be an empty page;
    it says what it is and which command makes a page (the browser's side of this is not run here)."""
    template = page.TEMPLATE.read_text(encoding="utf-8")
    assert template.count(page.DATA_PLACE) == 1
    handler = template[template.index('window.addEventListener("error"') : template.index('id="data"')]
    assert "This is the template of the catalog page" in handler and "statera docs --open" in handler
    assert "The page could not be drawn" in handler
    assert 'id="boot"' in template and "the script of the page did not run" in template


def test_the_lineage_puts_in_each_table_what_it_is_made_from_and_what_reads_it(repository):
    graph = lineage.Graph()
    graph.nodes = {
        "model.x.stg": lineage.Node("model.x.stg", "stg_records", "silver", "model", unit="sample", unit_kind="product"),
        "model.x.sample_by_uf": lineage.Node(
            "model.x.sample_by_uf", "sample_by_uf", "gold", "model", contract="gold-sample-by-uf", unit="sample",
            unit_kind="product",
        ),
        "analysis.x.by_uf": lineage.Node("analysis.x.by_uf", "query: by_uf", "consumer", "query"),
    }  # fmt: skip
    graph.edges = {("model.x.stg", "model.x.sample_by_uf"), ("model.x.sample_by_uf", "analysis.x.by_uf")}
    data = page.build(repository, graph=graph)

    table = next(t for t in data["tables"] if t["name"] == "gold.sample_by_uf")
    assert [(n["label"], n["to"]) for n in table["from"]] == [("stg_records", None)]
    assert [(n["label"], n["to"]) for n in table["feeds"]] == [("query: by_uf", ["queries", "by-uf"])]
    assert data["lineage"]["svg"].startswith("<svg") and data["lineage"]["missing"] is None
    assert next(p for p in data["products"] if p["name"] == "sample")["lineage"].startswith("<svg")


def test_without_the_production_tools_the_page_says_the_lineage_is_missing(repository, monkeypatch, capsys):
    def no_dbt(**_):
        raise RuntimeError("dbt is not installed in this environment. Reading the data does not need it.\nmore")

    monkeypatch.setattr(page.dbt, "manifest", no_dbt)
    opened = []
    monkeypatch.setattr("webbrowser.open", opened.append)

    assert cli.main(["docs", "--page"]) == 0
    out = capsys.readouterr()
    assert (
        out.err.strip() == "[statera] the lineage was left out of the page: dbt is not installed in this environment."
    )
    assert out.out.strip().endswith("target/catalog.html") and not opened
    assert '"missing": "dbt is not installed in this environment."' in page.path(repository).read_text(encoding="utf-8")

    assert cli.main(["docs", "--open"]) == 0  # --open writes the page too
    assert opened == [page.path(repository).as_uri()]


# --- the look of the page: a theme chosen when it is made ---------------------------------------------

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000001e221bc330000000049454e44ae426082"
)


@pytest.fixture
def institute(repository):
    """A theme folder inside the repository: name, notice, logo, colors for both modes, a font, links, CSS."""
    folder = repository.root / "governance/themes/institute"
    _write(
        folder / "theme.toml",
        """
        name = "Data catalog"
        notice = "Internal use"
        footer = "Data office <data@example.org>"
        favicon = "logo.png"

        [logo]
        file = "logo.png"
        dark = "logo-white.png"
        alt = "Example Institute"
        height = 36

        [colors]
        accent = "#1f5fa8"
        header = "#12355b"
        header_text = "#fff"
        [colors.dark]
        accent = "#8fbce8"
        background = "#101418"

        [fonts]
        text = "'Segoe UI', Arial, sans-serif"

        [home]
        title = "Data of the institute"

        [[links]]
        label = "Intranet"
        url = "https://intranet.example.org"
        """,
    )
    _write(folder / "theme.css", ".tile { border-radius: 4px; }")
    (folder / "logo.png").write_bytes(PNG)
    (folder / "logo-white.png").write_bytes(PNG)
    return folder


def test_a_theme_gives_the_page_an_institutions_look(repository, institute):
    look = page.themes.load(institute)
    assert look.data["name"] == "Data catalog" and look.data["notice"] == "Internal use"
    assert look.data["logo"]["src"].startswith("data:image/png;base64,iVBORw0KGgo") and look.data["logo"]["dark"]
    assert look.data["logo"]["alt"] == "Example Institute" and look.data["logo"]["alone"] is False
    assert look.data["links"] == [{"label": "Intranet", "url": "https://intranet.example.org"}]
    assert look.data["home"] == {"title": "Data of the institute"}
    assert look.favicon.startswith("data:image/png;base64,")

    light, dark_by_system, dark_fixed, own = look.css.split("\n", 3)
    assert light == (
        ":root { --accent: #1f5fa8; --top: #12355b; --top-ink: #fff; --accent-soft: #dce4ec; "
        "--font: 'Segoe UI', Arial, sans-serif; --logo-h: 36px; }"
    )  # accent_soft was not given: the accent, thin, over the page's background
    assert dark_by_system == (
        '@media (prefers-color-scheme: dark) { :root:not([data-mode="light"]) '
        "{ --accent: #8fbce8; --bg: #101418; --accent-soft: #222c35; } }"
    )
    assert dark_fixed == ':root[data-mode="dark"] { --accent: #8fbce8; --bg: #101418; --accent-soft: #222c35; }'
    assert own == "/* theme.css */\n.tile { border-radius: 4px; }"


def test_the_theme_travels_inside_the_page(repository, institute):
    """One file still: the logo and the favicon as data, the CSS as text, nothing by address."""
    output, _ = page.write(repository, graph=None, theme="governance/themes/institute")
    text = output.read_text(encoding="utf-8")
    assert '<html lang="en">' in text  # mode auto: the reader's system decides
    assert '<link rel="icon" href="data:image/png;base64,' in text
    assert '<style id="theme">:root { --accent: #1f5fa8;' in text and ".tile { border-radius: 4px; }</style>" in text
    block = re.search(r'<script type="application/json" id="data">(.*?)</script>', text, flags=re.S).group(1)
    assert json.loads(block)["theme"]["footer"] == "Data office <data@example.org>"
    assert "<data@" not in text  # what a theme says is text, never markup
    assert not re.search(r"""(src|href)\s*=\s*["']?(https?:)?//""", text.replace(block, ""))

    plain = page.render(page.build(repository, graph=None))  # no theme: the framework's look, and no trace of one
    assert '<style id="theme"></style>' in plain and "<link" not in plain and '"theme": {}' in plain


def test_which_theme_the_page_is_made_with(repository, institute, monkeypatch, capsys):
    assert page.themes.find(repository) is None  # the repository declares none
    assert page.themes.find(repository, "governance/themes/institute") == institute.resolve()
    monkeypatch.chdir(institute.parent)
    assert page.themes.find(repository, "institute") == institute.resolve()  # a path from where one is

    with (repository.root / "statera.toml").open("a", encoding="utf-8") as f:
        f.write('\n[page]\ntheme = "governance/themes/institute"\n')
    declared = Project(repository.root)
    assert page.themes.find(declared) == institute.resolve()  # the repository's own look
    assert page.themes.find(declared, "default") is None  # the framework's, whatever the repository declares
    assert [(f.ok, f.message) for f in validation.check_page_theme(declared)] == [
        (True, "catalog page theme: governance/themes/institute")
    ]

    (institute / "theme.toml").write_text('mode = "sepia"', encoding="utf-8")
    assert [f.ok for f in validation.check_page_theme(declared)] == [False]
    assert cli.main(["docs", "--theme", "nowhere"]) == 1
    assert "--theme: no theme folder at nowhere or" in capsys.readouterr().err


def test_a_theme_fixes_light_or_dark(repository, institute):
    (institute / "theme.toml").write_text('mode = "dark"', encoding="utf-8")
    look = page.themes.load(institute)
    assert look.mode == "dark" and '<html lang="en" data-mode="dark">' in page.render({"queries": []}, look)


@pytest.mark.parametrize(
    "toml, reason",
    [
        ('colour = "#fff"', "unknown key(s) colour; accepted: colors, favicon, "),
        ('[colors]\nprimary = "#fff"', "[colors] unknown key(s) primary"),
        (
            '[colors]\naccent = "red; } body { display: none"',
            "[colors] accent = 'red; } body { display: none' is not a color",
        ),
        ('[colors.dark]\naccent = "url(x)"', "[colors.dark] accent = 'url(x)' is not a color"),
        ('[fonts]\ntext = "Arial; } * { color: red"', "is not a list of font names"),
        ('mode = "sepia"', "mode = 'sepia'; accepted: auto, light, dark"),
        ('[logo]\nalt = "x"', "[logo] needs file"),
        ('[logo]\nfile = "missing.png"', "there is no such file"),
        ('[logo]\nfile = "../../../statera.toml"', "must name a file inside the theme's folder"),
        ('[logo]\nfile = "theme.css"', "is not an image the page takes"),
        ('[logo]\nfile = "logo.png"\nheight = 400', "height must be a whole number of pixels, from 12 to 120"),
        ('[[links]]\nlabel = "x"\nurl = "javascript:alert(1)"', "a url that is an address"),
        ('[[links]]\nurl = "https://example.org"', "needs a label"),
        ("name = [", "theme.toml: "),
    ],
)
def test_a_theme_that_cannot_be_used_says_where(institute, toml, reason):
    (institute / "theme.toml").write_text(toml, encoding="utf-8")
    with pytest.raises(page.themes.ThemeError) as error:
        page.themes.load(institute)
    assert reason in str(error.value) and "theme.toml" in str(error.value)


def test_a_themes_css_cannot_end_the_block_it_is_put_in(institute):
    (institute / "theme.css").write_text("a { color: red } </STYLE><script>alert(1)</script>", encoding="utf-8")
    with pytest.raises(page.themes.ThemeError, match="cannot contain </style>"):
        page.themes.load(institute)


def test_the_colors_a_theme_is_mixed_with_are_the_pages_own():
    """`accent_soft` is derived from the accent over the page's background: the two backgrounds the theme
    module knows must be the ones the page is drawn with."""
    template = page.TEMPLATE.read_text(encoding="utf-8")
    light, dark = re.findall(r"--bg: (#[0-9a-f]{6});", template)[:2]
    assert {"light": light, "dark": dark} == page.themes.BACKGROUND
    assert all(
        variable + ":" in template or variable in ("--top", "--top-ink") for variable in page.themes.COLORS.values()
    )
    assert "var(--top, var(--side))" in template and "var(--top-ink, var(--ink))" in template


def test_the_examples_theme_is_a_theme():
    folder = Path(__file__).parents[1] / "examples/foo-bar/content/governance/themes/institute"
    look = page.themes.load(folder)
    assert look.data["name"] == "Data catalog" and look.data["logo"]["src"].startswith("data:image/svg+xml;base64,")
    assert len(look.data["links"]) == 2 and "--top: #12355b;" in look.css and ".tile {" in look.css
