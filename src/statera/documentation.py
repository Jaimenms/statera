"""Generates browsable documentation from the content repository's metadata.

  docs/ (in the repository): only what changes when the repository changes — catalog, structural
  lineage, processes, rules, series, queries, metrics, recipes and conformance.
  <editions register>/<series>/README.md: what changes with each edition — editions, revisions,
  evaluation, lineage per version and observed duration.

Nothing is written by hand, so nothing goes stale. The files work on GitHub/GitLab (Markdown,
Mermaid and SVG) and in the VS Code Markdown preview.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import yaml

from statera import classification, contracts, generated, lineage
from statera.design import bpmn
from statera.disseminate import registry
from statera.edition import read_jsonl
from statera.process import dbt, rules
from statera.project import Project, Unit, current, read_yaml

AXES = ("theme", "product", "bases", "frequency", "purpose")


def _notice(project: Project) -> str:
    note = "**Example with SYNTHETIC DATA.**" if project.synthetic else None
    return generated.header(**_DOCS, style="md", note=note)


_DOCS = {
    "command": "statera docs",
    "sources": "repository metadata (contracts, products, processes, rules, series)",
    "regenerate": "statera docs",
    "ci": False,
}


def _link(project: Project, path: Path, text: str | None = None) -> str:
    rel = os.path.relpath(path, project.docs).replace(os.sep, "/")
    return f"[{text or path.name}]({rel})"


def _table(header: list[str], lines: list[list]) -> str:
    def cell(v) -> str:
        return str("" if v is None else v).replace("|", "\\|").replace("\n", " ")

    output = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    output += ["| " + " | ".join(cell(v) for v in line) + " |" for line in lines]
    return "\n".join(output)


def _write(project: Project, name: str, content: str) -> Path:
    destination = project.docs / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content.rstrip() + "\n", encoding="utf-8")
    return destination


def _product_axes(odps: dict) -> dict:
    return {c["property"]: c["value"] for c in odps.get("customProperties", [])}


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


ALIGNMENT_LABEL = {"source": "internal · source", "aggregate": "internal · aggregate", "consumer": "dissemination"}


def _mesh(project: Project) -> str:
    """Mermaid: internal and dissemination products, linked by their input ports."""
    from statera import ports

    def mid(name: str) -> str:
        return "p_" + name.replace("-", "_")

    lines = ["flowchart LR"]
    for u in project.products():
        shape = ('[("', '")]') if u.alignment == "source" else (('[["', '"]]') if u.internal else ('(["', '"])'))
        label = f"{u.odps.get('name', u.name)}<br/><small>{ALIGNMENT_LABEL[u.alignment]}</small>"
        lines.append(f"  {mid(u.name)}{shape[0]}{label}{shape[1]}")
    for u in project.products():
        for input_ in ports.input_products(u):
            lines.append(f"  {mid(input_.name)} -->|cut| {mid(u.name)}")
    lines += ["  classDef internal fill:#ECEFF4,stroke:#4C566A", "  classDef public fill:#FBF0C9,stroke:#9A7B0A"]
    internal_products = [mid(u.name) for u in project.products() if u.internal]
    public_products = [mid(u.name) for u in project.products() if not u.internal]
    lines += [f"  class {','.join(internal_products)} internal"] if internal_products else []
    lines += [f"  class {','.join(public_products)} public"] if public_products else []
    return "\n".join(lines)


def _section_units(project: Project) -> list[str]:
    from statera import conformance, ports

    levels = {r.unit.name: r for r in conformance.evaluate(project)}
    lines = []
    for u in project.products():
        cp = _product_axes(u.odps)
        inputs = ", ".join(e.name for e in ports.input_products(u)) or "—"
        r = levels[u.name]
        level = f"{r.achieved} — {conformance.NAMES.get(r.achieved, 'none')}" if r.achieved else "0"
        lines.append([_link(project, u.descriptor, u.odps["name"]), ALIGNMENT_LABEL[u.alignment], level,
                       f"`{u.urn}`", u.odps.get("domain", ""), inputs, cp.get("theme", ""),
                       ", ".join(cp.get("bases", [])), cp.get("frequency", "") or "per cut",
                       cp.get("purpose", "")])  # fmt: skip
    return [
        "## Products and the five organizing axes",
        "",
        "The catalog is organized by **theme, product, base, frequency and purpose**. The axes come from "
        "each product's ODPS descriptor. **Internal** products (source-aligned or aggregate) are not "
        "disseminated: they release **cuts** to the authorized consumers. The **level** is the conformance "
        "level achieved (1 cataloged to 4 evaluated; details in [conformance](conformance.md)).",
        "",
        _table(
            [
                "Product",
                "Kind",
                "Level",
                "Identifier",
                "Domain",
                "Reads the cuts of",
                "Theme",
                "Bases",
                "Frequency",
                "Purpose",
            ],
            lines,
        ),
        "",
        "### Product mesh",
        "",
        "```mermaid",
        _mesh(project),
        "```",
        "",
    ]


def _authorship(authors: list[dict] | None) -> str:
    return ", ".join(f"{a['name']} ({a['unit']})" if a.get("unit") else a["name"] for a in authors or [])


def _url(text: str, url: str | None) -> str:
    return f"[{text}]({url})" if url else text


def _section_access(project: Project, manifest: dict) -> list[str]:
    """Distributions (files) and visualizations (dashboards and charts) of each dissemination product."""
    from statera import access

    lines = []
    for u in project.products():
        if u.internal:
            continue
        for d in access.distributions(u):
            edition = _link(project, d.registry, d.edition) if d.registry and d.registry.exists() else (d.edition or "")
            origin = "generated with each edition" if d.origin == "series" else "produced outside the process"
            lines.append([u.odps["name"], _url(d.title, d.url), d.format_, d.updated, edition, origin])
    visualizations = access.visualizations(manifest)
    label = {"dashboard": "dashboard", "chart": "static charts"}
    viz_rows = []
    for e in visualizations:
        meta = e.get("meta") or {}
        tables = ", ".join(f"`{n.split('.')[-1]}`" for n in e["depends_on"]["nodes"])
        viz_rows.append([_url(e.get("label") or e["name"], e.get("url")), meta.get("product", ""),
                           label.get(meta.get("visualization"), ""), meta.get("tool", ""), meta.get("reads", ""),
                           meta.get("updated", ""), tables, (e.get("owner") or {}).get("name", ""),
                           _authorship(meta.get("authors")), e.get("maturity", "")])  # fmt: skip
    parts = [
        "## How to access",
        "",
        "Where the public finds each dissemination product. **Distributions** are the files (in DCAT "
        "vocabulary): those of products with a series come out of the process with each edition and are "
        "recorded, with a hash, in the editions register; those of level 1 products are declared in the ODPS "
        "descriptor (`distributions`), because they are still produced outside the process.",
        "",
        _table(["Product", "Distribution", "Format", "Updated", "Latest edition", "Origin"], lines),
        "",
    ]
    if viz_rows:
        parts += [
            "### Dashboards and charts",
            "",
            "Visualizations declared as consumers (dbt `exposures`). Public ones read the **published "
            "edition**, so they show the same number as the spreadsheet; the *Reads* column gives the read mode "
            "(`published-edition`, `cut-date` or `current`).",
            "",
            _table(
                [
                    "Visualization",
                    "Product",
                    "Kind",
                    "Tool",
                    "Reads",
                    "Updated",
                    "Tables",
                    "Owner",
                    "Authors",
                    "Maturity",
                ],
                viz_rows,
            ),  # fmt: skip
            "",
        ]
        for e in visualizations:
            charts = (e.get("meta") or {}).get("charts") or []
            if charts:
                parts += [
                    f"**{e.get('label') or e['name']}**: each image, the numbers it shows (as a table) and its "
                    "alternative text.",
                    "",
                    _table(
                        ["Chart", "Numbers as a table (port)", "Alternative text"],
                        [[g.get("title", ""), g.get("port", ""), g.get("alt_text", "")] for g in charts],
                    ),  # fmt: skip
                    "",
                ]
    return parts


def _section_authorizations(project: Project) -> list[str]:
    """Who may read each internal product, for what purpose and until when (authorizedConsumers)."""
    from statera import ports

    lines = []
    for u in project.products():
        if not u.internal:
            continue
        for item in u.custom_property("authorizedConsumers", []) or []:
            if item.get("role"):
                who, kind = item["role"], "team (reads in the organization's environment)"
                valid = "yes" if ports.current_authorization(u, item["role"]) else "**expired**"
            else:
                who = item.get("product") or item.get("query")
                kind, valid = ("product" if item.get("product") else "cataloged query"), "—"
            lines.append([u.odps["name"], f"`{who}`", kind, item.get("purpose", ""), item.get("until", "—"),
                           valid, item.get("act", "")])  # fmt: skip
    if not lines:
        return []
    return [
        "## Who may read the internal products",
        "",
        "Internal products hold restricted data, including personal data. Who reads them is declared in the "
        "product (`authorizedConsumers`), always with the purpose. A **team** that is not the owner reads in "
        "the organization's environment, until the authorized date and with the act that authorized it; once "
        "the term expires, CI fails until the authorization is renewed or withdrawn. Outside these "
        "authorizations, the path is a cataloged query (aggregated output, with disclosure control) or the "
        "deidentified microdata.",
        "",
        _table(["Internal product", "Who", "Kind", "Purpose", "Until", "Current", "Act"], lines),
        "",
    ]


def _section_references(project: Project) -> list[str]:
    lines = []
    for r in project.references():
        for path in r.contracts():
            c = read_yaml(path)
            prop = contracts.custom_property_of
            lines.append([
                _link(project, path, c["name"]), c["version"], prop(c, "temporality", "—"),
                prop(c, "authority", ""), prop(c, "temporalNote", ""),
            ])  # fmt: skip
    return [
        "## Reference tables over time",
        "",
        "Each reference table declares its temporality: **stable**; **validity** (each row is valid over an "
        "interval, and joins use the event date); **versioned** (versions published by the authority, pinned "
        "by the consumer, with a correspondence table between versions). Every edition records the version "
        "and hash of each reference table used.",
        "",
        _table(["Reference table", "Contract version", "Temporality", "Authority", "Note"], lines),
        "",
    ]


def _section_layers(project: Project, all_: dict) -> list[str]:
    parts = ["## Assets by layer", ""]
    for layer in ("bronze", "ref", "silver", "gold"):
        items = [(c, path) for path, c in all_.values() if contracts.layer(c) == layer]
        if not items:
            continue
        lines = []
        for c, path in sorted(items, key=lambda x: contracts.physical_table(x[0])):
            owner = (c.get("team") or {}).get("members", [{}])[0].get("username", "").removeprefix("role:")
            unit = project.unit_of(path)
            lines.append(
                [
                    f"`{contracts.physical_table(c)}`",
                    unit.name if unit else "",
                    " ".join(c["description"]["purpose"].split()),
                    classification.summary(classification.of_table(c)),
                    owner,
                    _link(project, path, c["id"]),
                ]
            )
        parts += [f"### {lineage.LAYER_LABEL[layer]}", ""]
        parts += [_table(["Asset", "Unit", "Purpose", "Classification", "Owner", "Contract"], lines), ""]
    return parts


def _section_contract(project: Project, cid: str, path: Path, c: dict) -> list[str]:
    obj = c["schema"][0]
    parts = [
        f"### {c['name']}",
        "",
        f"`{contracts.custom_property_of(c, 'urn')}` · `{cid}` · version {c['version']} · "
        f"{_link(project, path, 'contract file')}",
        "",
        f"- **Classification:** {classification.summary(classification.of_table(c))}",
        f"- **Granularity:** {obj.get('dataGranularityDescription', '')}",
        f"- **Limitations:** {' '.join(c['description'].get('limitations', '').split())}",
        "",
    ]
    lines = [
        [
            f"`{p['name']}`",
            p.get("logicalType"),
            "yes" if p.get("required") else "",
            p.get("classification"),
            "personal" if classification.personal_column(p) else "",
            p.get("description", ""),
        ]
        for p in obj["properties"]
    ]
    parts += [_table(["Column", "Type", "Required", "Sharing", "Personal data", "Description"], lines), ""]
    quality = list(obj.get("quality", []))
    quality += [q | {"column": p["name"]} for p in obj["properties"] for q in p.get("quality", [])]
    if quality:
        parts.append("**Quality rules:**")
        for q in quality:
            target = f" (`{q['column']}`)" if "column" in q else ""
            text = " ".join((q.get("description") or q.get("metric") or q.get("type")).split())
            parts.append(f"- `{q.get('id', q.get('metric'))}`{target}: {text}")
        parts.append("")
    return parts


def generate_catalog(project: Project) -> Path:
    all_ = contracts.all_(project)
    manifest = dbt.manifest(generate=False, project=project)
    parts = ["# Data catalog", "", _notice(project), *_section_units(project),
              *_section_access(project, manifest), *_section_authorizations(project), *_section_references(project),
              *_section_layers(project, all_)]  # fmt: skip
    parts += ["## Contract details", ""]
    for cid, (path, c) in sorted(all_.items()):
        parts += _section_contract(project, cid, path, c)
    return _write(project, "catalog.md", "\n".join(parts))


# ---------------------------------------------------------------------------
# Structural lineage
# ---------------------------------------------------------------------------


def _owner_text(owner: dict | None) -> str:
    if not owner:
        return ""
    return owner["name"] + (f" (`{owner['git_team']}`)" if owner["git_team"] else "")


def _impact_table(report: list[dict]) -> str:
    """One row per affected box: product (with owner), reference table, queries or external destinations."""
    rows = []
    for r in report:
        assets = ", ".join(f"{label} ({layer})" for layer, _kind, label in r["assets"])
        rows.append([r["title"] + (" — same product" if r["same_unit"] else ""), _owner_text(r["owner"]), assets])
    return _table(["Affected", "Owner (notify)", "Assets"], rows)


def _unit_links(names, kind: str = "product") -> str:
    return ", ".join(f"[{n}](lineage/{n}.md)" if kind == "product" else f"`{n}`" for n in sorted(names)) or "—"


def generate_lineage(project: Project) -> list[Path]:
    from statera import governance

    g = lineage.build()
    svg = _write(
        project,
        "lineage.svg",
        generated.header(**_DOCS, style="xml") + lineage.to_svg(g, f"Data lineage by layer — {project.name}"),
    )
    mermaid = lineage.to_mermaid(g)
    _write(project, "lineage.mmd", generated.header(**_DOCS, style="%%") + mermaid)
    edges = lineage.unit_edges(g)
    products = [u for u in project.products() if g.of_unit(u.name)]
    rows = []
    for u in products:
        reads = {a for a, b in edges if b == u.name}
        read_by = {b for a, b in edges if a == u.name}
        refs = {r for r in reads if not any(p.name == r for p in project.products())}
        rows.append([
            f"[{u.name}](lineage/{u.name}.md)", u.alignment, _owner_text(governance.unit_owner(u)),
            _unit_links(reads - refs), _unit_links(refs, "reference"), _unit_links(read_by),
        ])  # fmt: skip
    parts = [
        "# Data lineage",
        "",
        _notice(project),
        "Generated from dbt's `manifest.json`, the ODCS contracts, the declared consumers (`exposures`) "
        "and the recurring queries. Each box is a product (in its color, the same as in the dbt catalog), a "
        "reference table or a group outside the products; the nodes are colored by layer. Lineage **per "
        "version** (which extraction each number of each edition came from) is on the series page in the "
        "editions register.",
        "",
        "## All products",
        "",
        "```mermaid",
        mermaid,
        "```",
        "",
        "## Products",
        "",
        "Each product's page shows it with everything upstream and downstream, and who to notify when it "
        "changes (`statera lineage --product <name>`, `statera lineage --impact <name>`). In the dbt catalog "
        "(`statera docs --interactive`), select `+tag:product:<name>+`.",
        "",
        _table(["Product", "Alignment", "Owner", "Reads products", "Reads reference tables", "Read by"], rows),
        "",
        "<details><summary>Same lineage by layer (SVG)</summary>",
        "",
        "![Lineage by layer](lineage.svg)",
        "",
        "</details>",
        "",
    ]
    seeds = [n for n in g.nodes.values() if n.kind == "seed"]
    # impact example: the reference table with the most downstream assets
    reference = max(seeds, key=lambda n: (len(lineage.impact(g, n.id)), n.label), default=None)
    if reference:
        parts += [
            "## Impact analysis",
            "",
            f"Example: what needs to be reviewed, and by whom, if the reference table `{reference.label}` "
            f"changes (`statera lineage --impact {reference.unit or reference.label}`).",
            "",
            _impact_table(lineage.impact_report(g, reference.unit or reference.id)),
            "",
        ]
    written = [svg, _write(project, "lineage.md", "\n".join(parts))]
    written += [_product_lineage(project, g, u) for u in products]
    for stale in (project.docs / "lineage").glob("*.md"):  # products that no longer exist
        if stale not in written:
            stale.unlink()
    return written


def _product_lineage(project: Project, g, unit: Unit) -> Path:
    """One product with everything upstream and downstream of it, and the impact of a change to it."""
    from statera import governance

    edges = lineage.unit_edges(g)
    upstream = lineage.by_group(g, lineage.upstream(g, set(g.of_unit(unit.name))))
    sources = []
    for group, ids in upstream.items():
        kind, _, name = group.partition(":")
        if kind == "product" and name != unit.name:
            sources.append(f"product [{name}]({name}.md)")
        elif kind == "reference":
            sources.append(f"reference table `{name}`")
        elif kind == "systems":
            sources.append("source system " + ", ".join(g.nodes[i].label for i in ids))
    report = lineage.impact_report(g, unit.name)
    others = [r for r in report if not r["same_unit"]]
    teams = sorted({r["owner"]["git_team"] for r in others if r["owner"] and r["owner"]["git_team"]})
    parts = [
        f"# Lineage — {unit.name}",
        "",
        _notice(project),
        f"{unit.odps.get('name', unit.name)} · {unit.alignment}{' · internal' if unit.internal else ''} · "
        f"owner {_owner_text(governance.unit_owner(unit))}. [All products](../lineage.md).",
        "",
        "```mermaid",
        lineage.to_mermaid(lineage.neighborhood(g, unit.name), focus=unit.name),
        "```",
        "",
        "## Reads",
        "",
        "; ".join(sources) + "." if sources else "Nothing outside the product.",
        "",
        "## Impact of a change",
        "",
        f"What is downstream of `{unit.name}` (`statera lineage --impact {unit.name}`)"
        + (f"; notify {', '.join(f'`{t}`' for t in teams)}." if teams else "."),
        "",
        _impact_table(report) if report else "Nothing reads this product.",
        "",
    ]
    if read_by := sorted({b for a, b in edges if a == unit.name}):
        parts += [f"Products that read it directly: {_unit_links(read_by).replace('lineage/', '')}.", ""]
    return _write(project, f"lineage/{unit.name}.md", "\n".join(parts))


# ---------------------------------------------------------------------------
# Processes
# ---------------------------------------------------------------------------


def _task_row(p: bpmn.Process, t: bpmn.Element) -> list:
    impl = t.attrs.get("implementation", "")
    return [
        t.name,
        p.lanes.get(t.lane, {}).get("name", ""),
        t.kind,
        t.attrs.get("gsbpm"),
        f"{t.duration:.0f} min",
        f"`{impl}`" if impl else "",
        "exception" if t.attrs.get("exception") == "true" else "",
    ]


def generate_processes(project: Project) -> list[Path]:
    generated_files = bpmn.render_all()
    parts = [
        "# Processes (BPMN 2.0 annotated with GSBPM)",
        "",
        _notice(project),
        "Each product has **one process**, a **YAML mirror of BPMN 2.0** in `<product>/2-design/process.yaml`. "
        "`statera bpmn` generates the standard `.bpmn` (Camunda Modeler, bpmn.io, VS Code extension) and the "
        "images below. The process is also the **execution plan**: `statera run` walks the flows, "
        "runs the automatic tasks and stops at the human ones.",
        "",
    ]
    processes = bpmn.all_()
    summaries = []
    for p in processes:
        r = bpmn.summary(p)
        summaries.append(
            [
                p.name,
                r["tasks"],
                f"{r['manual_h']:.1f} h",
                f"{r['semi-automatic_h']:.1f} h",
                f"{r['automatic_h']:.2f} h",
                f"{r['total_h']:.1f} h",
            ]
        )
    parts += [
        "## Effort per edition",
        "",
        _table(["Process", "Tasks", "Manual", "Semi-automatic", "Automatic", "Total"], summaries),
        "",
        "Modeled durations (`statera:durationMin`), on the normal path, without exception tasks. The observed "
        "duration is on the series page in the editions register.",
        "",
    ]
    for p in processes:
        svg = bpmn.svg_destination(p)
        parts += [
            f"## {p.name}",
            "",
            f"Source: {_link(project, p.path.with_suffix('.yaml'))} · generated: {_link(project, p.path)} · "
            f"[interactive viewer](processes/{svg.with_suffix('.html').name})",
            "",
            f"![{p.name}](processes/{svg.name})",
            "",
            _table(
                ["Task", "Lane (role)", "Kind", "GSBPM", "Duration", "Implementation", ""],
                [_task_row(p, t) for t in p.tasks],
            ),
            "",
        ]
    return [*generated_files, _write(project, "processes.md", "\n".join(parts))]


# ---------------------------------------------------------------------------
# Rules and series
# ---------------------------------------------------------------------------


def generate_rules(project: Project) -> Path:
    parts = [
        "# Business rules",
        "",
        _notice(project),
        "Each rule has a prose definition (`rule.md`) and **executable cases** (`cases.yaml`), in the unit "
        "that applies it: in the aggregate internal product, if it holds for all consumers; in the "
        "dissemination product, if it defines that product's scope. The cases become dbt *unit tests* "
        "(`statera rules sync`).",
        "",
    ]
    entries = rules.load(project)
    lines = [
        [
            _link(project, r.folder / "rule.md", r.title),
            r.unit.name if r.unit else "",
            f"`{r.model}`",
            len(r.cases),
        ]
        for r in entries
    ]
    parts += [_table(["Rule", "Unit", "dbt model", "Cases"], lines), ""]
    for r in entries:
        parts += [
            f"## {r.title}",
            "",
            f"Implementation: `{r.model}` · {_link(project, r.folder / 'cases.yaml', 'cases')}",
            "",
        ]
        for case in r.cases:
            parts += [
                f"- **{case['id']}** — {case['description']}",
                "",
                "  <details><summary>inputs and expected output</summary>",
                "",
                "  ```yaml",
            ]
            body = yaml.safe_dump(
                {"inputs": case["inputs"], "expected": case["expected"]},
                allow_unicode=True,
                sort_keys=False,
                width=200,
            )
            parts += ["  " + line for line in body.splitlines()]
            parts += ["  ```", "", "  </details>", ""]
    return _write(project, "rules.md", "\n".join(parts))


def generate_series(project: Project) -> Path:
    parts = [
        "# Publication series",
        "",
        _notice(project),
        "Each series describes what **every** edition publishes. Editions are recorded by the process, outside "
        f"this repository (`{os.path.relpath(project.edition_registry, project.root)}`, in statera.toml), "
        "so that publishing "
        "does not require a commit to the code.",
        "",
    ]
    for u, s in registry.series(project):
        odps = u.read("1-specify/product.odps.yaml")
        edition_files = s["files_per_edition"]
        tables = [edition_files["tables"].format(port=p["name"]) for p in odps["outputPorts"]]
        reg = s["edition_registry"]
        gov = s.get("open_data_portal") or {}
        parts += [
            f"## {s['title']}",
            "",
            f"Source: {_link(project, registry.series_file(u))} · summary template: "
            f"{_link(project, u.phase(7) / 'summary.md.j2')}",
            "",
            _table(
                ["Item", "Value"],
                [
                    ["Product", _link(project, u.descriptor, odps["name"])],
                    ["Frequency", s["frequency"]],
                    ["Start", s.get("start", "")],
                    ["License", s["license"].get("title", s["license"]["name"])],
                    ["Open data portal", f"{gov.get('organization', '')} / {gov.get('dataset', '')}" if gov else ""],
                    [
                        "Files per edition",
                        ", ".join(
                            [
                                *tables,
                                edition_files["spreadsheet"],
                                edition_files["summary"],
                                *edition_files.get("metadata", []),
                            ]
                        ),
                    ],
                    ["Main port", s["main_resource"]],
                    ["Record of each edition", f"`{reg['edition']}`"],
                    ["Series index", f"`{reg['index']}`"],
                    ["Who writes the record", reg["written_by"]],
                ],
            ),
            "",
            f"**Coverage.** {' '.join(s.get('coverage', '').split())}",
            "",
            f"**Revision.** {' '.join(s['revision_policy'].split())}",
            "",
            f"**Correction.** {' '.join(s['correction_policy'].split())}",
            "",
        ]
    return _write(project, "series.md", "\n".join(parts))


# ---------------------------------------------------------------------------
# Consumption layer: queries, metrics, recipes
# ---------------------------------------------------------------------------


def generate_queries(project: Project) -> Path:
    from statera.consume import queries

    entries = queries.load(project)
    parts = [
        "# Recurring queries",
        "",
        _notice(project),
        "Queries that recur, standardized, parameterized, tested and organized by the five catalog axes. "
        "Each one is a dbt *analysis* (it appears in the lineage) with metadata in `query.yaml`. "
        "Run: `statera query run <id> --param name=value`.",
        "",
    ]
    lines = [
        [
            _link(project, c.folder / "query.yaml", c.title),
            c.meta.get("theme"),
            c.meta.get("product") or "shared",
            ", ".join(c.meta.get("bases", [])),
            c.meta.get("frequency"),
            c.meta.get("purpose"),
            c.meta.get("status"),
        ]
        for c in entries
    ]
    parts += [_table(["Query", "Theme", "Product", "Bases", "Frequency", "Purpose", "Status"], lines), ""]
    for c in entries:
        parameters = [
            [
                f"`{p['name']}`",
                p.get("type"),
                "yes" if p.get("required") else "",
                p.get("default", ""),
                p.get("description", ""),
            ]
            for p in c.meta.get("parameters", [])
        ]
        output = c.meta.get("output_classification", {})
        parts += [
            f"## {c.title}",
            "",
            f"`{c.meta['id']}` · version {c.meta.get('version')} · owner `{c.meta.get('owner')}` · "
            f"{_link(project, c.sql, 'SQL')}"
            + (f" · authors: {_authorship(c.meta.get('authors'))}" if c.meta.get("authors") else ""),
            "",
            " ".join(c.meta.get("description", "").split()),
            "",
            _table(["Parameter", "Type", "Required", "Default", "Description"], parameters) if parameters else "",
            "",
            f"- **Rules applied:** {', '.join(c.meta.get('rules', [])) or '—'}",
            f"- **Limitations:** {' '.join(c.meta.get('limitations', '').split())}",
            f"- **Output classification:** {classification.summary(output) if output else '—'}",
            "",
        ]
    return _write(project, "queries.md", "\n".join(parts))


def generate_metrics(project: Project) -> Path:
    from statera.consume import metrics

    parts = [
        "# Metrics",
        "",
        _notice(project),
        "Each metric is defined **once**, with its allowed dimensions, in a format inspired by the dbt/MetricFlow "
        "semantic layer. Compute: `statera metric <name> --by <dimension>`, or "
        "`catalog.open().metric(...)` in Python.",
        "",
    ]
    lines = [
        [
            f"`{m['name']}`",
            m.get("label"),
            m["unit"].name,
            f"`{m['table']}`",
            f"`{m.get('expression') or m.get('type')}`",
            ", ".join(f"`{d}`" for d in m.get("dimensions", [])),
            " ".join(m.get("description", "").split()),
        ]
        for m in metrics.definitions(project).values()
    ]
    parts += [_table(["Metric", "Label", "Unit", "Table", "Calculation", "Dimensions", "Definition"], lines), ""]
    return _write(project, "metrics.md", "\n".join(parts))


def generate_recipes(project: Project) -> Path:
    from statera.consume import recipes

    parts = [
        "# Recipes",
        "",
        _notice(project),
        "Short, complete examples for common tasks. Each recipe runs in CI against the synthetic data "
        "(`statera recipes test`): a recipe that breaks does not pass.",
        "",
    ]
    lines = [
        [
            _link(project, r.path, r.title),
            r.meta.get("layers", ""),
            r.meta.get("classification", ""),
            r.path.suffix,
            r.meta.get("authors", ""),
        ]
        for r in recipes.load(project)
    ]
    parts += [_table(["Recipe", "Layers read", "Classification", "Format", "Authors"], lines), ""]
    return _write(project, "recipes.md", "\n".join(parts))


def generate_conformance(project: Project) -> Path:
    from statera import conformance

    parts = [
        "# Conformance",
        "",
        _notice(project),
        "Conformance levels of the specification (1 cataloged, 2 reproducible, 3 controlled, 4 evaluated). "
        "Each product declares its target level in `product.odps.yaml`; CI requires the target.",
        "",
    ]
    lines, outstanding = [], []
    for r in conformance.evaluate(project):
        lines.append([r.unit.name, r.target, r.achieved, "yes" if r.achieved >= r.target else "**no**"])
        outstanding += [f"- **{r.unit.name}**, level {n}: {m}" for n, m in r.outstanding]
    parts += [_table(["Product", "Target", "Achieved", "Target met"], lines), ""]
    if outstanding:
        parts += ["## Outstanding items for the next level", "", *outstanding, ""]
    return _write(project, "conformance.md", "\n".join(parts))


# ---------------------------------------------------------------------------
# Series page in the editions register (changes with each edition; outside the repository)
# ---------------------------------------------------------------------------


def _section_revisions(unit: Unit, dps: list[dict]) -> list[str]:
    from statera.analyse import controls
    from statera.extend.framework import CONTROL_TYPES

    main = registry.series_config(unit)["main_resource"]
    definition = next(
        (
            c
            for c in (unit.read("6-analyse/controls.yaml") or {}).get("controls", [])
            if (c.get("check") or CONTROL_TYPES.get(c.get("type", ""))) == "RevisionBetweenEditions"
            and c.get("port") == main
        ),
        None,
    )
    if len(dps) < 2 or definition is None:
        return []
    last, prev = dps[-1]["x-statera"], dps[-2]["x-statera"]
    current_, previous = (registry.published_table(unit, x["period"], main) for x in (last, prev))
    if current_ is None or previous is None:
        return []
    rev = controls.revisions(current_, previous, definition["key"], definition["measures"], definition["relative_base"])
    rev = rev[rev[f"revision_{definition['relative_base']}"] != 0]
    columns = [*definition["key"], *(f"{m}{s}" for m in definition["measures"] for s in ("_previous", ""))]
    return [
        f"## Revisions between editions {prev['period']} and {last['period']}",
        "",
        f"Port `{main}`, cut-off dates {prev['cutDate']} and {last['cutDate']} (control {definition['id']}).",
        "",
        _table(columns, rev[columns].values.tolist()),
        "",
    ]


def _section_evaluation(unit: Unit) -> list[str]:
    from statera.evaluate import evaluation

    history = evaluation.history(unit)
    if history.empty:
        return []
    names = {d["id"]: d["name"] for d in evaluation.indicators(unit)["indicators"]}
    lines = [[f"{c} {names.get(c, '')}", *history[c].tolist()] for c in history.columns if c != "period"]
    latest = history["period"].iloc[-1]
    return [
        "## Evaluation of the editions (GSBPM 8)",
        "",
        f"Indicators declared in `8-evaluate/indicators.yaml`. ⚠ = alert. "
        f"Report for the latest edition: [evaluations/{latest}.md](evaluations/{latest}.md).",
        "",
        _table(["Indicator", *history["period"].tolist()], lines),
        "",
    ]


def _section_version_lineage(dp: dict) -> list[str]:
    x = dp["x-statera"]
    cuts = [[name, c["cutDate"], f"`{c['run']}`"] for name, c in (x.get("cuts") or {}).items()]
    extractions = [
        [
            e["source"],
            e.get("sourceSystem", ""),
            e["cutDate"],
            e.get("batch", ""),
            e["rows"],
            f"`{e['sha256'][:16]}…`",
        ]
        for e in x.get("extractions", [])
    ]
    refs = [
        [r["reference"], r.get("version") or "", r.get("temporality") or "", r.get("authority") or "",
         r.get("via") or "(the product itself)", f"`{(r.get('sha256') or '')[:16]}…`"]
        for r in x.get("references", [])
    ]  # fmt: skip
    files = [
        [r["path"], (r.get("x-statera") or {}).get("contract", ""), f"`{r['hash'][7:23]}…`"] for r in dp["resources"]
    ]
    parts = [
        f"## Lineage per version — edition {x['period']} (version {dp['version']})",
        "",
        f"Cut-off date **{x['cutDate']}**, OpenLineage run `{x.get('run')}`, code "
        f"`{x['codeVersion']}`. Each published file is traceable to the exact bronze batches:",
        "",
    ]
    if cuts:
        parts += [_table(["Input internal product", "Cut", "Run that released the cut"], cuts), ""]
    parts += [
        _table(["Extraction (bronze)", "Source system", "Cut-off date", "Up to batch", "Rows", "SHA-256"], extractions),
        "",
    ]
    if refs:
        parts += [
            _table(["Reference table", "Contract version", "Temporality", "Authority", "Used in", "SHA-256"], refs),
            "",
        ]
    return [*parts, _table(["Published file", "Contract", "SHA-256"], files), ""]


def _section_duration(unit: Unit) -> list[str]:
    runs = pd.DataFrame(read_jsonl(unit.runs_folder / "runs.jsonl"))
    if runs.empty:
        return []
    automatic = runs[(runs["status"] == "ok") & runs["duration_s"].notna()]
    obs = automatic.groupby("bpmn_task")["duration_s"].agg(["count", "mean", "max"]).reset_index()
    lines = [[r.bpmn_task, r.count, f"{r.mean:.1f}", f"{r.max:.1f}"] for r in obs.itertuples()]
    return [
        "## Observed duration of the automatic tasks",
        "",
        "From the process run log, to compare with the duration modeled in BPMN.",
        "",
        _table(["BPMN task", "Runs", "Mean (s)", "Max (s)"], lines),
        "",
    ]


def generate_registry(unit: Unit) -> Path | None:
    dps = registry.editions(unit)
    if not dps:
        return None
    index = registry.index_path(unit)
    lines = []
    for dp in dps:
        x = dp["x-statera"]
        file = f"{x['period']}.datapackage.json"
        corrects = (x.get("corrects") or {}).get("version", "")
        lines.append([x["period"], dp["version"], x["cutDate"], dp["created"], corrects, f"[{file}]({file})"])
    synthetic = " **SYNTHETIC DATA.**" if unit.project.synthetic else ""
    parts = [
        f"# {registry.series_config(unit)['title']} — published editions",
        "",
        f"> Page GENERATED by the process with each published edition. Do not edit by hand.{synthetic}",
        "",
        f"Series index: [{index.name}]({index.name}).",
        "",
        _table(["Period", "Version", "Cut-off date", "Published on", "Corrects", "Record"], lines),
        "",
        *_section_evaluation(unit),
        *_section_revisions(unit, dps),
        *_section_version_lineage(dps[-1]),
        *_section_duration(unit),
    ]
    destination = index.parent / "README.md"
    destination.write_text("\n".join(parts).rstrip() + "\n", encoding="utf-8")
    return destination


# ---------------------------------------------------------------------------
# Index and main entry point
# ---------------------------------------------------------------------------


def generate_index(project: Project) -> Path:
    from statera import validation

    findings = validation.collect_findings(generate_manifest=False)
    failures = sum(not a.ok for a in findings)
    pages = [
        ["[Catalog](catalog.md)", "Units by the 5 catalog axes, assets by layer, ODCS contracts"],
        ["[Lineage](lineage.md)", "All products together and one page per product; impact of a change, by product"],
        ["[Processes](processes.md)", "Each product's process, BPMN with GSBPM; it is also the execution plan"],
        ["[Rules](rules.md)", "Business rules with executable cases, by unit"],
        ["[Series](series.md)", "What each edition publishes and where editions are recorded"],
        ["[Queries](queries.md)", "Cataloged, parameterized and tested recurring queries"],
        ["[Metrics](metrics.md)", "Official metrics defined once, with allowed dimensions"],
        ["[Recipes](recipes.md)", "Executable, tested examples for those who analyze the data"],
        ["[Conformance](conformance.md)", "Conformance level of each product and outstanding items"],
    ]
    parts = [
        f"# Generated documentation — {project.name}",
        "",
        _notice(project),
        f"Organization: {project.organization} · namespace `{project.namespace}` · statera specification "
        f"{project.config['project'].get('specification', '?')}.",
        "",
        _table(["Page", "Contents"], pages),
        "",
        "Only what changes when the repository changes goes here. What changes with each edition (published "
        "editions, revisions, lineage per version, observed duration) is on the series page in the editions "
        "register.",
        "",
        f"Validation status at generation time: **{len(findings) - failures} checks ok, "
        f"{failures} failures** (`statera validate`).",
        "",
    ]
    return _write(project, "README.md", "\n".join(parts))


def generate_interactive(project: Project | None = None) -> Path:
    """Browsable, searchable catalog: `dbt docs` as a single page (`--static`), with the models, the
    reference tables, the consumers and visualizations, the lineage and each table's `meta`
    (product, contract, URN and classification). It lives in target/ (outside git), since it contains
    paths of the machine that generated it; it opens directly in the browser, without a server."""
    project = project or current()
    dbt.ensure_packages(project)
    process = dbt.run(["docs", "generate", "--static"], project=project)
    if process.returncode != 0:
        raise RuntimeError(f"dbt docs generate failed:\n{process.stdout[-3000:]}")
    return project.dbt_project / "target" / "static_index.html"


def generate() -> list[Path]:
    project = current()
    dbt.manifest(generate=True, project=project)
    files = [
        generate_catalog(project),
        *generate_lineage(project),
        *generate_processes(project),
        generate_rules(project),
        generate_series(project),
        generate_queries(project),
        generate_metrics(project),
        generate_recipes(project),
        generate_conformance(project),
    ]
    records = [generate_registry(u) for u, _ in registry.series(project)]
    return [*files, generate_index(project), *filter(None, records)]
