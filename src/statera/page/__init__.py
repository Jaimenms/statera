"""The catalog as one page: everything the repository declares, to browse and to search, in a single file.

    statera docs --page          # writes target/catalog.html
    statera docs --page --open   # and opens it in the browser
    statera docs --page --theme governance/themes/intranet   # with an institution's look (statera.page.theme)

It is the one door for someone who analyzes the data: what there is (products, tables and their contracts,
published editions), what can be asked of it (recurring queries with a form for their parameters, official
metrics, recipes) and how each number is made (business rules with their cases, the code in SQL, Python, R
and SPSS, the lineage, each product's process).

The page is a VIEW of the repository, not a second source: `build` gathers the data through the loaders the
commands already use (`queries.load`, the catalog's contracts, `rules.load`, the editions register, the
lineage graph) and `write` puts it, as JSON, inside `page.html`. One file, opened from disk: no server, no
network, nothing fetched from outside. It holds metadata and code only, never data (the cases shown under a
rule or a query are synthetic by definition). It lives in target/ (outside git): the Markdown in docs/
remains what a pull request reviews.

A query's parameters are filled in the browser by the rules of `queries.validate_parameters` (the same
types, the same defaults), and a query cannot be copied while a value is missing or invalid. That is only
possible when the SQL is plain `ref()` and `var()`; a query that computes part of its SQL from a parameter
(`{% if %}`, a filter on `var()`) is shown as written, with the command that fills it.
"""

from __future__ import annotations

import html
import json
import os
import re
from datetime import date
from pathlib import Path

import jinja2
from jinja2 import nodes

import statera
from statera import classification, contracts, generated, launcher
from statera.consume import catalog, metrics, queries, recipes
from statera.page import theme as themes
from statera.page.theme import Theme
from statera.process import dbt, rules
from statera.project import Project, Unit, current, read_yaml

OPEN, CLOSE = "", ""  # around a parameter's place in the SQL: the page puts the value there
LANGUAGES = {".sql": "sql", ".py": "python", ".R": "r", ".r": "r", ".sps": "spss"}
LAYER_ORDER = {"gold": 0, "ref": 1, "silver": 2, "bronze": 3}
TEMPLATE = Path(__file__).with_name("page.html")
HEAD_PLACE, THEME_PLACE, DATA_PLACE = (
    "<!--STATERA-CATALOG-HEAD-->",
    "/*STATERA-CATALOG-THEME*/",
    "/*STATERA-CATALOG-DATA*/",
)
COMMANDS = launcher.CONSUMPTION_COMMANDS | {launcher.COPILOT_COMMAND}  # what someone who only reads can run
UNSET = object()


def _clean(text) -> str:
    return " ".join(str(text or "").split())


def _rel(project: Project, path: Path) -> str:
    return project.rel(path)


# --- Markdown: the little of it a rule.md uses, with no library ---------------------------------------


def _inline(text: str) -> str:
    """Code, bold, emphasis and links of one line. Only http(s) links stay links: a link to a file of the
    repository leads nowhere from target/."""
    parts = re.split(r"(`[^`]+`)", text)
    out = []
    for part in parts:
        if part.startswith("`") and part.endswith("`") and len(part) > 1:
            out.append(f"<code>{html.escape(part[1:-1])}</code>")
            continue
        s = html.escape(part, quote=False)
        s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
        s = re.sub(r"(?<![\w*])\*([^*\s][^*]*?)\*(?![\w*])", r"<i>\1</i>", s)
        s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2" rel="noreferrer">\1</a>', s)
        s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)
        out.append(s)
    return "".join(out)


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def markdown(text: str) -> str:
    """Headings, paragraphs, lists, tables, quotes and fenced code, as HTML (everything else is escaped)."""
    lines, out, i = text.splitlines(), [], 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
        elif line.startswith("```"):
            end = next((j for j in range(i + 1, len(lines)) if lines[j].startswith("```")), len(lines))
            out.append(f"<pre>{html.escape(chr(10).join(lines[i + 1 : end]))}</pre>")
            i = end + 1
        elif m := re.match(r"(#{1,6})\s+(.*)", line):
            level = min(len(m.group(1)) + 1, 6)  # the page's own title is the h2 above
            out.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
            i += 1
        elif line.lstrip().startswith("|") and i + 1 < len(lines) and re.match(r"\s*\|[\s:|-]+\|\s*$", lines[i + 1]):
            head, rows, i = _cells(line), [], i + 2
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                rows.append(_cells(lines[i]))
                i += 1
            body = "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in rows)
            out.append(f"<table><tr>{''.join(f'<th>{_inline(c)}</th>' for c in head)}</tr>{body}</table>")
        elif m := re.match(r"\s*([-*]|\d+\.)\s+", line):
            tag, items = ("ol" if m.group(1)[0].isdigit() else "ul"), []
            while i < len(lines) and (item := re.match(r"\s*(?:[-*]|\d+\.)\s+(.*)", lines[i])):
                text_, i = [item.group(1)], i + 1
                while i < len(lines) and lines[i].startswith("  ") and lines[i].strip():
                    text_.append(lines[i].strip())
                    i += 1
                items.append(f"<li>{_inline(' '.join(text_))}</li>")
            out.append(f"<{tag}>{''.join(items)}</{tag}>")
        elif line.startswith(">"):
            quote = []
            while i < len(lines) and lines[i].startswith(">"):
                quote.append(lines[i].lstrip("> "))
                i += 1
            out.append(f"<blockquote>{_inline(' '.join(quote))}</blockquote>")
        else:
            paragraph = []
            while (
                i < len(lines)
                and lines[i].strip()
                and not re.match(r"(#{1,6}\s|```|>|\s*\||\s*([-*]|\d+\.)\s)", lines[i])
            ):
                paragraph.append(lines[i].strip())
                i += 1
            if not paragraph:  # a line that looks like a table or a list but is neither
                paragraph, i = [line.strip()], i + 1
            out.append(f"<p>{_inline(' '.join(paragraph))}</p>")
    return "\n".join(out)


# --- queries ------------------------------------------------------------------------------------------


def fillable(source: str) -> bool:
    """True when the SQL is text with `ref('…')` and `var('…'[, default])` and nothing else of Jinja: then
    each parameter has a place a value can be put in. With a condition, a loop or a filter, the SQL itself
    depends on the value, and only the framework can fill it (`statera query show`)."""
    try:
        tree = jinja2.Environment().parse(source)
    except jinja2.TemplateError:
        return False
    for node in tree.body:
        if not isinstance(node, nodes.Output):
            return False
        for part in node.nodes:
            if isinstance(part, nodes.TemplateData):
                continue
            call = (
                isinstance(part, nodes.Call) and isinstance(part.node, nodes.Name) and part.node.name in ("ref", "var")
            )
            if not call or part.kwargs or part.dyn_args or part.dyn_kwargs:
                return False
            if not all(isinstance(a, nodes.Const) for a in part.args):
                return False
    return True


def with_places(query: queries.Query, relations: dict[str, str]) -> tuple[str | None, dict]:
    """The query's SQL with ref() resolved and each declared parameter left as a marked place, and the
    defaults the SQL itself gives to var(). None when the SQL cannot be filled in the browser."""
    source = query.sql.read_text(encoding="utf-8")
    declared, defaults = {p["name"] for p in query.meta.get("parameters", [])}, {}
    if not fillable(source):
        return None, {}

    def var(name, default=None):
        if name not in declared:  # as `queries.render` does: an undeclared var() is its own default
            return default
        defaults[name] = default
        return f"{OPEN}{name}{CLOSE}"

    environment = jinja2.Environment(undefined=jinja2.StrictUndefined, autoescape=False)
    try:
        return environment.from_string(source).render(ref=lambda n: relations[n], var=var).strip(), defaults
    except (KeyError, jinja2.TemplateError):  # a ref() to a model that does not exist: validation says so
        return None, {}


def _formatted(sql: str | None) -> tuple[str | None, list[str]]:
    """The SQL laid out by sqlglot (it comes with Ibis), and the columns the query returns."""
    if sql is None:
        return None, []
    try:
        import sqlglot

        tree = sqlglot.parse_one(sql, read="duckdb")
        columns = [c for c in tree.named_selects if c and c != "*"]
        return tree.sql(dialect="duckdb", pretty=True), columns
    except Exception:  # noqa: BLE001 (SQL that sqlglot cannot read is shown as written)
        return None, []


def _queries(project: Project, column_types: dict[str, dict]) -> list[dict]:
    relations, out = dbt.relations(project), []
    for q in queries.load(project):
        meta = q.meta
        written, sql_defaults = with_places(q, relations)
        formatted, columns = _formatted(written)
        types = {name: kind for base in meta.get("bases", []) for name, kind in column_types.get(base, {}).items()}
        tests = (read_yaml(q.test_file) or {}).get("cases", []) if q.test_file.exists() else []
        out.append(
            {
                "slug": q.folder.name,
                "id": q.id,
                "title": q.title,
                "description": _clean(meta.get("description")),
                "limitations": _clean(meta.get("limitations")),
                "theme": meta.get("theme"),
                "product": meta.get("product"),
                "bases": meta.get("bases", []),
                "frequency": meta.get("frequency"),
                "purpose": meta.get("purpose"),
                "rules": meta.get("rules", []),
                "owner": meta.get("owner"),
                "status": meta.get("status"),
                "version": meta.get("version"),
                "authors": [_clean(a.get("name")) for a in meta.get("authors", []) or []],
                "classification": classification.summary(meta.get("output_classification") or {}),
                "parameters": [
                    {
                        "name": p["name"],
                        "type": p["type"],
                        "required": bool(p.get("required")),
                        "default": p.get("default"),
                        "description": _clean(p.get("description")),
                        "format": queries.FORMATS.get(p["type"], p["type"]),
                        "sql_default": sql_defaults.get(p["name"]),
                    }
                    for p in meta.get("parameters", [])
                ],
                "template": q.sql.read_text(encoding="utf-8").strip(),
                "written": written,
                "formatted": formatted,
                "columns": [{"name": c, "type": types.get(c)} for c in columns],
                "tests": tests,
                "files": [_rel(project, f) for f in (q.folder / "query.yaml", q.sql, q.test_file) if f.exists()],
            }
        )
    return out


# --- tables, metrics, rules, code ---------------------------------------------------------------------


def _tables(project: Project, cat: catalog.Catalog) -> list[dict]:
    out = []
    for name in sorted(cat._contracts, key=lambda t: (LAYER_ORDER.get(t.split(".")[0], 9), t)):
        path, contract = cat._contracts[name]
        try:
            d = cat.describe(name)
        except KeyError:  # a contract without a description: `statera validate` says so
            continue
        unit = project.unit_of(path)
        props = {p["name"]: p for p in contract["schema"][0]["properties"]}
        keys = {p["name"] for p in props.values() if p.get("primaryKey")}
        node = contracts.dbt_node(contract)
        out.append(
            {
                "name": name,
                "title": _clean(contract.get("name")),
                "urn": d.urn,
                "contract": contract.get("id"),
                "version": contract.get("version"),
                "file": _rel(project, path),
                "unit": d.unit,
                "unit_kind": unit.kind if unit else None,
                "internal": bool(unit and unit.internal),
                "layer": d.layer,
                "purpose": d.purpose,
                "usage": d.usage,
                "limitations": d.limitations,
                "classification": d.classification,
                "grain": _clean(contract["schema"][0].get("dataGranularityDescription")),
                "notes": [n for n in cat.notes(name) if not n.startswith("grain:")],
                "caution": cat._caution(name, contract),
                "model": node[1] if node else None,
                "columns": [{**c, "key": c["name"] in keys} for c in d.columns],
            }
        )
    return out


def _metrics(project: Project) -> list[dict]:
    out = []
    for name, m in metrics.definitions(project).items():
        out.append(
            {
                "name": name,
                "label": m.get("label", name),
                "description": _clean(m.get("description")),
                "type": m.get("type"),
                "unit": m["unit"].name,
                "aggregation": m.get("aggregation"),
                "expression": m.get("expression"),
                "numerator": m.get("numerator"),
                "denominator": m.get("denominator"),
                "factor": m.get("factor", 1),
                "time": m.get("time"),
                "sources": [{"table": s["table"], "dimensions": list(s["dimensions"])} for s in m.get("sources", [])],
            }
        )
    return out


def _rules(project: Project) -> list[dict]:
    out = []
    for rule in rules.load(project):
        text = (rule.folder / "rule.md").read_text(encoding="utf-8")
        _, _, body = text.partition("\n")
        out.append(
            {
                "id": rule.id,
                "title": re.sub(rf"^{re.escape(rule.id)}\s*[—–:-]\s*", "", rule.title),
                "unit": rule.unit.name if rule.unit else None,
                "model": rule.model,
                "summary": _definition(body),
                "html": markdown(body),
                "cases": [
                    {
                        "id": c.get("id"),
                        "description": _clean(c.get("description")),
                        "inputs": c.get("inputs") or {},
                        "expected": c.get("expected") or [],
                    }
                    for c in rule.cases
                ],
                "requires": rule.requires,
                "path": _rel(project, rule.folder / "rule.md"),
            }
        )
    return out


def _definition(body: str) -> str:
    """What the rule says, in a few lines: the paragraph under its "Definition" heading, else its first one."""
    found = re.search(r"^#+\s*Definition\s*\n+(.+?)(?:\n\s*\n|\Z)", body, flags=re.S | re.M | re.I)
    if not found:
        plain = [b for b in re.split(r"\n\s*\n", body) if b.strip() and not b.lstrip().startswith(("|", "#", "```"))]
        return _clean(re.sub(r"[*`]", "", plain[0]))[:300] if plain else ""
    return _clean(re.sub(r"[*`]", "", found.group(1)))[:300]


def _about(text: str) -> str:
    """What a file says it is, in its first comment line."""
    first = text.lstrip().splitlines()[0] if text.strip() else ""
    stripped = re.sub(r'^(#+|--+|\*+|"""|/\*+)\s*', "", first).rstrip('*/" ').strip()
    return stripped if stripped != first.strip() else ""


def _code(project: Project, relations: dict[str, str]) -> list[dict]:
    """The code that makes the numbers: each product's models (SQL, Python, and the R and SPSS they call)
    and the checks of its analysis phase."""
    out = []
    for unit in project.products():
        for role, folder, pattern in (("model", unit.phase(5) / "models", "**/*"), ("check", unit.phase(6), "*")):
            for file in sorted(folder.glob(pattern)) if folder.is_dir() else []:
                if file.suffix not in LANGUAGES or file.name.startswith(("_", ".")):
                    continue
                text = file.read_text(encoding="utf-8").strip()
                out.append(
                    {
                        "path": _rel(project, file),
                        "name": file.name,
                        "unit": unit.name,
                        "role": role,
                        "language": LANGUAGES[file.suffix],
                        "about": _about(text),
                        "table": relations.get(file.stem) if role == "model" else None,
                        "text": text,
                    }
                )
    return out


def _recipes(project: Project) -> list[dict]:
    return [
        {
            "path": _rel(project, r.path),
            "name": r.path.name,
            "title": r.title,
            "layers": r.meta.get("layers", ""),
            "classification": r.meta.get("classification", ""),
            "authors": r.meta.get("authors", ""),
            "language": LANGUAGES.get(r.path.suffix, "sql"),
            "text": r.path.read_text(encoding="utf-8").strip(),
        }
        for r in recipes.load(project)
    ]


# --- products: what each one is, its calendar, what it published --------------------------------------


def _svg(path: Path) -> str | None:
    """A generated drawing, without the generated-file notice (a comment) it starts with."""
    if not path.exists():
        return None
    return re.sub(r"<!--.*?-->", "", path.read_text(encoding="utf-8"), flags=re.S).strip()


def _editions(unit: Unit) -> list[dict]:
    from statera.disseminate import registry

    out = []
    for dp in registry.editions(unit):
        x = dp.get("x-statera") or {}
        out.append(
            {
                "period": x.get("period"),
                "version": dp.get("version"),
                "cut_date": x.get("cutDate"),
                "recorded": str(dp.get("created") or "")[:10],
                "corrects": (x.get("corrects") or {}).get("version"),
                "files": [{"name": r.get("name"), "format": r.get("format")} for r in dp.get("resources", [])],
            }
        )
    return sorted(out, key=lambda e: str(e["period"]), reverse=True)


def _calendar(unit: Unit) -> dict | None:
    calendar = unit.read("1-specify/calendar.yaml")
    if not calendar:
        return None
    try:
        from statera.execution import next_expected

        expected = next_expected(unit)
    except Exception:  # noqa: BLE001 (the calendar is still shown without what comes next)
        expected = None
    rows = [
        {"edition": str(e.get("period")), "cut_date": str(e.get("cut_date")), "release": str(e.get("release") or "")}
        for e in calendar.get("editions") or []
    ] + [
        {"edition": str(c.get("cut_date")), "cut_date": str(c.get("cut_date")), "release": ""}
        for c in calendar.get("cuts") or []
    ]
    return {
        "frequency": calendar.get("frequency"),
        "cut_date_rule": _clean(calendar.get("cut_date_rule")),
        "release_rule": _clean(calendar.get("release_rule")),
        "rows": rows,
        "next": expected,
    }


def _series(unit: Unit) -> dict | None:
    from statera.disseminate import registry

    if not unit.has("7-disseminate/series.yaml"):
        return None
    s = registry.series_config(unit)
    return {
        "title": _clean(s.get("title")),
        "page": s.get("page"),
        "license": (s.get("license") or {}).get("name"),
        "coverage": _clean(s.get("coverage")),
        "revision_policy": _clean(s.get("revision_policy")),
        "correction_policy": _clean(s.get("correction_policy")),
        "editions": _editions(unit),
    }


def _distributions(unit: Unit) -> list[dict]:
    from statera import access

    if unit.internal:
        return []
    try:
        found = access.distributions(unit)
    except Exception:  # noqa: BLE001 (a series or a register that validation would refuse)
        return []
    return [
        {"title": d.title, "format": d.format_, "url": d.url, "updated": d.updated, "edition": d.edition} for d in found
    ]


def _products(project: Project, graph) -> list[dict]:
    from statera import lineage

    out = []
    for unit in project.products():
        odps = unit.odps
        process = unit.read("2-design/process.yaml") if unit.process.name == "process.yaml" else None
        drawing = None
        if graph is not None and graph.of_unit(unit.name):
            drawing = lineage.to_svg(
                lineage.neighborhood(graph, unit.name), title=f"{unit.name}: what it reads and what reads it"
            )
        out.append(
            {
                "name": unit.name,
                "title": odps.get("name", unit.name),
                "internal": unit.internal,
                "alignment": unit.alignment,
                "version": odps.get("version"),
                "status": odps.get("status"),
                "domain": odps.get("domain"),
                "team": (odps.get("team") or {}).get("name"),
                "conformance": unit.custom_property("conformanceLevel"),
                "tags": odps.get("tags") or [],
                "description": {k: _clean(v) for k, v in (odps.get("description") or {}).items() if v},
                "outputs": [
                    {
                        "name": p.get("name"),
                        "description": _clean(p.get("description")),
                        "contract": p.get("contractId"),
                    }
                    for p in odps.get("outputPorts", [])
                ],
                "authorized": [
                    {
                        "who": a.get("role") or a.get("product") or a.get("query"),
                        "kind": "team" if a.get("role") else "product" if a.get("product") else "query",
                        "purpose": _clean(a.get("purpose")),
                        "until": str(a.get("until") or ""),
                    }
                    for a in unit.custom_property("authorizedConsumers", []) or []
                ],
                "process": {"name": (process or {}).get("name"), "svg": _svg(unit.phase(2) / "process.svg")},
                "calendar": _calendar(unit),
                "series": _series(unit),
                "distributions": _distributions(unit),
                "lineage": drawing,
                "file": _rel(project, unit.descriptor) if unit.descriptor else None,
            }
        )
    return out


# --- lineage and the command line ---------------------------------------------------------------------


def _graph(project: Project):
    """The lineage graph, or the reason there is none (it needs dbt: `statera docs` installs it)."""
    from statera import lineage

    try:
        return lineage.build(dbt.manifest(generate=True, project=project)), None
    except Exception as error:  # noqa: BLE001 (the page is still worth having without the lineage)
        first = str(error).splitlines()[0] if str(error) else type(error).__name__
        return None, re.split(r"(?<=[.!?])\s", _clean(first), maxsplit=1)[0][:300]


def _neighbours(graph, tables: list[dict], found: list[dict], code: list[dict]) -> None:
    """Puts in each table what it is made from and what reads it, each with where the page shows it."""
    if graph is None:
        return
    by_contract = {t["contract"]: t["name"] for t in tables}
    by_query = {q["slug"].replace("-", "_"): q["slug"] for q in found}
    by_model = {Path(c["path"]).stem: c["path"] for c in code if c["role"] == "model"}

    def shown(node_id: str) -> dict:
        node = graph.nodes[node_id]
        to = None
        if node.contract in by_contract:
            to = ["tables", by_contract[node.contract]]
        elif node.kind == "query" and node_id.rsplit(".", 1)[-1] in by_query:
            to = ["queries", by_query[node_id.rsplit(".", 1)[-1]]]
        elif node.label in by_model:
            to = ["code", by_model[node.label]]
        return {"label": node.label, "layer": node.layer, "kind": node.kind, "url": node.url, "to": to}

    node_of = {n.contract: i for i, n in graph.nodes.items() if n.contract}
    for t in tables:
        if node_id := node_of.get(t["contract"]):
            t["from"] = [shown(i) for i in graph.parents(node_id)]
            t["feeds"] = [shown(i) for i in graph.children(node_id)]


def _commands(parser) -> list[dict]:
    """The commands someone who only reads the data can run, as the command line itself describes them."""
    import argparse

    if parser is None:
        return []
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    helps = {a.dest: a.help or "" for a in sub._choices_actions}
    width, os.environ["COLUMNS"] = os.environ.get("COLUMNS"), "100"  # the same text on every terminal
    try:
        return [
            {"name": name, "summary": _clean(helps.get(name)), "help": sub.choices[name].format_help().strip()}
            for name in sorted(COMMANDS & set(sub.choices))
        ]
    finally:
        if width is None:
            del os.environ["COLUMNS"]
        else:
            os.environ["COLUMNS"] = width


# --- the page -----------------------------------------------------------------------------------------


def build(project: Project | None = None, parser=None, graph=UNSET) -> dict:
    """Everything the page shows, as plain data. `parser`: the command line's (for the commands page);
    `graph`: the lineage graph, or None to leave the lineage out (by default it is built, which needs dbt)."""
    project = project or current()
    reason = None
    if graph is UNSET:
        graph, reason = _graph(project)
    cat = catalog.open(project)
    relations = dbt.relations(project)
    tables = _tables(project, cat)
    found = _queries(project, {t["name"]: {c["name"]: c["type"] for c in t["columns"]} for t in tables})
    code = _code(project, relations)
    _neighbours(graph, tables, found, code)
    info = project.config.get("project", {})
    return {
        "project": {
            "name": project.name,
            "organization": info.get("organization"),
            "namespace": project.namespace,
            "synthetic": project.synthetic,
            "generated": date.today().isoformat(),
            "statera": statera.__version__,
            "conventions": project.conventions,
        },
        "products": _products(project, graph),
        "references": [{"name": u.name} for u in project.references()],
        "tables": tables,
        "queries": found,
        "metrics": _metrics(project),
        "rules": _rules(project),
        "recipes": _recipes(project),
        "code": code,
        "commands": _commands(parser),
        "lineage": {"svg": _lineage_svg(project, graph), "missing": reason},
        "validators": {k: v.pattern for k, v in queries.VALIDATORS.items()},
        "examples": queries._EXAMPLES,
    }


def _lineage_svg(project: Project, graph) -> str | None:
    """The drawing of every product together; without the graph, the one `statera docs` last left in docs/."""
    from statera import lineage

    if graph is None:
        return _svg(project.docs / "lineage.svg")
    return lineage.to_svg(graph, title="Data lineage") if graph.nodes else None


def render(data: dict, look: Theme | None = None) -> str:
    """The page with its data and its look inside. `<` is written as an escape, so nothing in the
    repository (a query, a description) can end the script block it travels in. The template is cut at its
    three places first and put together after, so what fills one place is never read as another."""
    look = look or Theme()
    text = json.dumps({**data, "theme": look.data}, ensure_ascii=False, default=str).replace("<", "\\u003c")
    notice = generated.header(
        "statera docs --page",
        "repository metadata and code (contracts, products, queries, metrics, rules, models, editions register)",
        style="xml",
        regenerate="statera docs --page",
        ci=False,
    )
    head, rest = TEMPLATE.read_text(encoding="utf-8").split(HEAD_PLACE)
    style, rest = rest.split(THEME_PLACE)
    body, tail = rest.split(DATA_PLACE)
    if look.mode != "auto":  # the theme fixes light or dark, whatever the reader's system says
        head = head.replace('<html lang="en">', f'<html lang="en" data-mode="{look.mode}">', 1)
    icon = f'<link rel="icon" href="{look.favicon}">' if look.favicon else ""
    return generated.as_html(head + icon + style + look.css + body + text + tail, notice)


def path(project: Project | None = None) -> Path:
    """target/catalog.html: outside git, like dbt's own artifacts (it is rebuilt, never reviewed)."""
    return (project or current()).dbt_project / "target" / "catalog.html"


def write(
    project: Project | None = None, parser=None, graph=UNSET, theme: str | None = None, output: Path | None = None
) -> tuple[Path, dict]:
    """Writes the page; returns where, and the data it was built from. `theme`: the folder of the look to
    give it (statera.page.theme); by default the repository's own, else the framework's. `output`: another
    file to write it to (a folder the intranet serves; docs/ to keep it in git), or a folder: a path with no
    extension is one, and the page is `catalog.html` in it."""
    project = project or current()
    look = themes.of(project, theme)  # first: a theme that cannot be used stops here, before the slow part
    data = build(project, parser, graph)
    output = Path(output).absolute() if output else path(project)
    if output.is_dir() or not output.suffix:
        output = output / "catalog.html"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(data, look), encoding="utf-8")
    return output, data
