"""Lineage graph: sources -> bronze -> silver -> gold -> consumers.

Built from dbt's `manifest.json` (sources, seeds, models, exposures) and enriched with the ODCS
contracts (contract and classification of each asset). Source systems come from the bronze
contracts (`extraction.sourceSystem`); external destinations, from the exposures'
`meta.external_destination`; recurring queries (dbt analyses) appear as consumers.
Every node knows the unit it belongs to (product or reference table, from the file that declares it), so
the same graph is read at two levels: assets, and products.
Outputs:
  - Mermaid grouped by product (rendered natively by GitHub and by VS Code extensions): all products
    together, or one product with everything upstream and downstream of it;
  - SVG with a layered layout, no external dependencies (shows up in any viewer);
  - impact analysis: everything downstream of a node or of a whole product, grouped by product.
"""

from __future__ import annotations

import html
import json
from collections import defaultdict
from dataclasses import dataclass, field

from statera import classification, contracts
from statera.project import current

LAYER_ORDER = ["source", "bronze", "ref", "silver", "gold", "consumer"]
LAYER_LABEL = {
    "source": "Source systems",
    "bronze": "Bronze",
    "ref": "Reference tables (ref)",
    "silver": "Silver",
    "gold": "Gold",
    "consumer": "Consumers",
}
COLORS = {  # background, border
    "source": ("#ECEFF4", "#4C566A"),
    "bronze": ("#F6E3D3", "#9C5B2E"),
    "ref": ("#E1ECF7", "#2F6497"),
    "silver": ("#EDEDED", "#6B6B6B"),
    "gold": ("#FBF0C9", "#9A7B0A"),
    "consumer": ("#EAE2F5", "#5E3C99"),
}


@dataclass
class Node:
    id: str
    label: str
    layer: str
    kind: str
    contract: str | None = None
    classification: str | None = None
    description: str = ""
    url: str | None = None  # visualizations and destinations with a public address
    unit: str | None = None  # product or reference table that declares the node
    unit_kind: str | None = None  # "product" | "reference"

    @property
    def group(self) -> str:
        """Box the node is drawn in: its product or reference table, or a box outside every unit."""
        if self.unit:
            return f"{self.unit_kind}:{self.unit}"
        return {"system": "systems", "external": "external", "query": "queries"}.get(self.kind, "other")


@dataclass
class Graph:
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: set[tuple[str, str]] = field(default_factory=set)

    def parents(self, node: str) -> list[str]:
        return sorted(a for a, b in self.edges if b == node)

    def children(self, node: str) -> list[str]:
        return sorted(b for a, b in self.edges if a == node)

    def of_unit(self, unit: str) -> list[str]:
        return sorted(uid for uid, n in self.nodes.items() if n.unit == unit)

    def subgraph(self, ids: set[str]) -> Graph:
        return Graph({i: self.nodes[i] for i in ids}, {(a, b) for a, b in self.edges if a in ids and b in ids})


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def _contract_index() -> tuple[dict[str, dict], dict[str, dict]]:
    by_model, by_source = {}, {}
    for _, c in contracts.all_().values():
        if m := contracts.custom_property_of(c, "dbtModel") or contracts.custom_property_of(c, "dbtSeed"):
            by_model[m] = c
        if s := contracts.custom_property_of(c, "dbtSource"):
            by_source[s] = c
    return by_model, by_source


def _annotate(node: Node, contract: dict | None) -> Node:
    if contract:
        node.contract = contract["id"]
        dims = classification.of_table(contract)
        node.classification = f"{dims.get('sharing', '?')} · {dims.get('nature', '?')}"
    return node


def _model_layer(node: dict) -> str:
    meta = (node.get("config", {}).get("meta") or {}) | (node.get("meta") or {})
    return meta.get("layer", "silver")


def _add_sources(g: Graph, manifest: dict, by_source: dict) -> None:
    for uid, s in manifest["sources"].items():
        key = f"{s['source_name']}.{s['name']}"
        node = Node(uid, f"bronze.{s['name']}", "bronze", "source", description=s.get("description", ""))
        g.nodes[uid] = _annotate(node, by_source.get(key))
        extraction = contracts.custom_property_of(by_source.get(key) or {}, "extraction") or {}
        system_name = extraction.get("sourceSystem", s["source_name"])
        system = "system." + "".join(c if c.isalnum() else "_" for c in system_name.lower())
        g.nodes.setdefault(system, Node(system, system_name, "source", "system"))
        g.edges.add((system, uid))


def _add_dbt_nodes(g: Graph, manifest: dict, by_model: dict) -> None:
    for uid, n in manifest["nodes"].items():
        kind = n["resource_type"]
        if kind not in ("seed", "model"):
            continue
        layer = "ref" if kind == "seed" else _model_layer(n)
        node = Node(uid, n["name"], layer, kind, description=n.get("description", ""))
        g.nodes[uid] = _annotate(node, by_model.get(n["name"]))
    for uid, n in manifest["nodes"].items():
        if n["resource_type"] == "model":
            g.edges.update((parent, uid) for parent in n["depends_on"]["nodes"] if parent in g.nodes)


def _add_exposures(g: Graph, manifest: dict) -> None:
    for uid, e in manifest["exposures"].items():
        meta = e.get("meta") or {}
        g.nodes[uid] = Node(
            uid,
            e.get("label") or e["name"],
            "consumer",
            f"exposure:{e['type']}",
            classification=meta.get("classification"),
            description=e.get("description", ""),
            url=e.get("url"),
        )
        g.edges.update((parent, uid) for parent in e["depends_on"]["nodes"] if parent in g.nodes)
        if destination := meta.get("external_destination"):
            external = "external." + "".join(c if c.isalnum() else "_" for c in destination.lower())
            g.nodes[external] = Node(external, destination, "consumer", "external")
            g.edges.add((uid, external))


def _add_queries(g: Graph, manifest: dict) -> None:
    """Recurring queries (dbt analyses): consumers that appear in the lineage."""
    for uid, n in manifest["nodes"].items():
        if n["resource_type"] != "analysis":
            continue
        g.nodes[uid] = Node(uid, f"query: {n['name']}", "consumer", "query", description=n.get("description", ""))
        g.edges.update((parent, uid) for parent in n["depends_on"]["nodes"] if parent in g.nodes)


def _assign_units(g: Graph, manifest: dict) -> None:
    """The unit of each asset is the product or reference table whose folder holds the file that declares it."""
    project = current()
    entries = {**manifest["nodes"], **manifest["sources"], **manifest["exposures"]}
    for uid, node in g.nodes.items():
        path = (entries.get(uid) or {}).get("original_file_path")
        if path and (unit := project.unit_of(project.root / path)):
            node.unit, node.unit_kind = unit.name, unit.kind


def build(manifest: dict | None = None) -> Graph:
    if manifest is None:
        manifest = json.loads(current().dbt_manifest.read_text())
    by_model, by_source = _contract_index()
    g = Graph()
    _add_sources(g, manifest, by_source)
    _add_dbt_nodes(g, manifest, by_model)
    _add_exposures(g, manifest)
    _add_queries(g, manifest)
    _assign_units(g, manifest)
    return g


def locate(g: Graph, term: str) -> str:
    if term in g.nodes:
        return term
    candidates = [
        uid for uid, n in g.nodes.items() if n.label == term or uid.endswith(f".{term}") or n.contract == term
    ]
    if not candidates:
        raise KeyError(f"Node '{term}' not found in the lineage")
    return candidates[0]


def _reach(g: Graph, start: set[str], step) -> set[str]:
    seen, stack = set(), list(start)
    while stack:
        for nxt in step(stack.pop()):
            if nxt not in seen and nxt not in start:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def downstream(g: Graph, ids: set[str]) -> set[str]:
    return _reach(g, ids, g.children)


def upstream(g: Graph, ids: set[str]) -> set[str]:
    return _reach(g, ids, g.parents)


def starting_nodes(g: Graph, term: str) -> set[str]:
    """A whole product or reference table (by name), or one asset (see `locate`)."""
    if nodes := g.of_unit(term):
        return set(nodes)
    return {locate(g, term)}


def impact(g: Graph, term: str) -> list[str]:
    """All downstream nodes of an asset or of a whole unit, ordered by rank (from nearest to farthest)."""
    ranks = _ranks(g)
    return sorted(downstream(g, starting_nodes(g, term)), key=lambda n: (ranks[n], g.nodes[n].label))


def impact_report(g: Graph, term: str) -> list[dict]:
    """Downstream of an asset or of a whole unit, one entry per box (product first), with the owner of each
    affected product: what to review and whom to notify."""
    from statera import governance

    project = current()
    start = starting_nodes(g, term)
    origin = {g.nodes[i].unit for i in start}
    report = []
    for group, ids in by_group(g, downstream(g, start)).items():
        kind, _, name = group.partition(":")
        owner = governance.unit_owner(project.unit(name, kind)) if kind in ("product", "reference") else None
        report.append({
            "group": group, "title": group_title(group), "kind": kind, "name": name or group,
            "same_unit": name in origin, "owner": owner,
            "assets": [(g.nodes[i].layer, g.nodes[i].kind, g.nodes[i].label) for i in ids],
        })  # fmt: skip
    return report


def by_group(g: Graph, ids) -> dict[str, list[str]]:
    """Node ids grouped by box (product, reference table, systems, queries, external), in drawing order."""
    groups: dict[str, list[str]] = defaultdict(list)
    for i in ids:
        groups[g.nodes[i].group].append(i)
    order = group_order(g)
    return {k: sorted(groups[k], key=lambda i: g.nodes[i].label) for k in sorted(groups, key=order.index)}


def neighborhood(g: Graph, unit: str) -> Graph:
    """One product (or reference table) with everything upstream and downstream of it."""
    own = set(g.of_unit(unit))
    return g.subgraph(own | upstream(g, own) | downstream(g, own))


def unit_edges(g: Graph) -> set[tuple[str, str]]:
    """Dependencies between units (product -> product, reference -> product) derived from the assets."""
    return {
        (g.nodes[a].unit, g.nodes[b].unit)
        for a, b in g.edges
        if g.nodes[a].unit and g.nodes[b].unit and g.nodes[a].unit != g.nodes[b].unit
    }


def group_order(g: Graph) -> list[str]:
    """Drawing order: source systems, products from upstream to downstream, reference tables, queries,
    external destinations."""
    products = {n.unit for n in g.nodes.values() if n.unit_kind == "product"}
    edges = {(a, b) for a, b in unit_edges(g) if a in products and b in products}
    depth: dict[str, int] = {}

    def level(p: str, path: frozenset = frozenset()) -> int:
        if p not in depth:
            parents = [a for a, b in edges if b == p and a not in path]
            depth[p] = 0 if not parents else 1 + max(level(a, path | {p}) for a in parents)
        return depth[p]

    ordered = sorted(products, key=lambda p: (level(p), p))
    references = sorted({n.unit for n in g.nodes.values() if n.unit_kind == "reference"})
    groups = ["systems", *(f"product:{p}" for p in ordered), *(f"reference:{r}" for r in references)]
    return [*groups, "queries", "external", "other"]


# ---------------------------------------------------------------------------
# Mermaid
# ---------------------------------------------------------------------------


def _mid(uid: str) -> str:
    return "n_" + "".join(c if c.isalnum() else "_" for c in uid)


GROUP_LABEL = {
    "systems": "Source systems",
    "queries": "Cross-cutting queries",
    "external": "External destinations",
    "other": "Other",
}


def group_title(group: str) -> str:
    kind, _, name = group.partition(":")
    if kind == "product":
        try:
            unit = current().product(name)
            return f"{name} · {unit.alignment}{' · internal' if unit.internal else ''}"
        except Exception:  # noqa: BLE001
            return name
    if kind == "reference":
        return f"{name} · reference table"
    return GROUP_LABEL.get(group, group)


def to_mermaid(g: Graph, focus: str | None = None) -> str:
    """One box per product (in its color), reference table and outside group; nodes colored by layer.
    With `focus`, that product's box is highlighted and the others are dashed."""
    project = current()
    colors = project.product_colors()
    lines = ["flowchart LR"]
    for group, ids in by_group(g, g.nodes).items():
        lines.append(f'  subgraph {_mid("g_" + group)}["{group_title(group)}"]')
        for n in (g.nodes[i] for i in ids):
            extra = f"<br/><small>{n.layer} · {n.classification}</small>" if n.classification else ""
            extra = extra or (f"<br/><small>{n.layer}</small>" if n.layer not in ("source", "consumer") else "")
            opener, closer = ('[("', '")]') if n.kind in ("source", "seed") else ('["', '"]')
            lines.append(f"    {_mid(n.id)}{opener}{n.label}{extra}{closer}")
        lines.append("  end")
    lines += [f"  {_mid(a)} --> {_mid(b)}" for a, b in sorted(g.edges)]
    for group in by_group(g, g.nodes):
        kind, _, name = group.partition(":")
        color = colors.get(name) if kind == "product" else "#8C8C8C" if kind == "reference" else "#B0B0B0"
        width = "4px" if focus and name == focus else "2px"
        dashed = ",stroke-dasharray:6 4" if focus and name != focus else ""
        lines.append(f"  style {_mid('g_' + group)} fill:{color}14,stroke:{color},stroke-width:{width}{dashed}")
    lines += [
        f'  click {_mid(n.id)} href "{n.url}" _blank' for n in sorted(g.nodes.values(), key=lambda n: n.id) if n.url
    ]
    for layer, (background, border) in COLORS.items():
        lines.append(f"  classDef {layer} fill:{background},stroke:{border},color:#1F1F1F")
        ids = [_mid(n.id) for n in g.nodes.values() if n.layer == layer]
        if ids:
            lines.append(f"  class {','.join(ids)} {layer}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# SVG with a layered layout, top to bottom (simplified Sugiyama)
# ---------------------------------------------------------------------------


def _ranks(g: Graph) -> dict[str, int]:
    rank: dict[str, int] = {}

    def compute(n: str) -> int:
        if n not in rank:
            parents = g.parents(n)
            rank[n] = 0 if not parents else 1 + max(compute(p) for p in parents)
        return rank[n]

    for n in g.nodes:
        compute(n)
    # Reference tables and other roots that are not source systems sit next to whoever uses them
    for n, node in g.nodes.items():
        if not g.parents(n) and node.layer != "source" and g.children(n):
            rank[n] = max(1, min(rank[f] for f in g.children(n)) - 1)
    # Consumers always in the last rows, below all of gold
    base = 1 + max((p for n, p in rank.items() if g.nodes[n].layer != "consumer"), default=0)
    for n, node in g.nodes.items():
        if node.layer == "consumer":
            rank[n] = base + (1 if node.kind == "external" else 0)
    return rank


def _barycenter(g: Graph, n: str, position: dict[str, int]) -> float:
    neighbors = g.parents(n) + g.children(n)
    return sum(position[v] for v in neighbors) / len(neighbors) if neighbors else position[n]


def _order_ranks(g: Graph, ranks: dict[str, int]) -> dict[int, list[str]]:
    lines: dict[int, list[str]] = defaultdict(list)
    initial_key = {n: (LAYER_ORDER.index(g.nodes[n].layer), g.nodes[n].label) for n in g.nodes}
    for n in sorted(g.nodes, key=initial_key.get):
        lines[ranks[n]].append(n)
    for _ in range(6):  # barycenter sweeps, to reduce crossings
        for target in sorted(lines):
            position = {n: i for line in lines.values() for i, n in enumerate(line)}
            weights = {n: _barycenter(g, n, position) for n in lines[target]}
            lines[target].sort(key=weights.get)
    return lines


def _draw_node(node: Node, x: float, y: float, w: float, h: float) -> list[str]:
    background, border = COLORS[node.layer]
    cylinder = node.kind in ("source", "seed")
    if cylinder:
        shape = (
            f'<path d="M{x},{y + 6} v{h - 12} a{w / 2},6 0 0 0 {w},0 v-{h - 12} '
            f'a{w / 2},6 0 0 0 -{w},0 a{w / 2},6 0 0 0 {w},0" fill="{background}" '
            f'stroke="{border}" stroke-width="1.4"/>'
        )
    else:
        shape = (
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="7" fill="{background}" '
            f'stroke="{border}" stroke-width="1.4"/>'
        )
    offset = 5 if cylinder else 0
    label = node.label if len(node.label) <= 34 else node.label[:32] + "…"
    parts = [
        shape,
        f'<text x="{x + w / 2}" y="{y + 20 + offset}" font-size="11.5" '
        f'font-weight="bold" text-anchor="middle" fill="#1F1F1F">{html.escape(label)}</text>',
    ]
    sub = " · ".join(filter(None, [node.classification, "ODCS" if node.contract else None]))
    if sub:
        parts.append(
            f'<text x="{x + w / 2}" y="{y + 35 + offset}" font-size="10" '
            f'text-anchor="middle" fill="{border}">{html.escape(sub)}</text>'
        )
    if node.url:
        parts = [f'<a href="{html.escape(node.url, quote=True)}" target="_blank">', *parts, "</a>"]
    return parts


def to_svg(g: Graph, title: str = "Data lineage") -> str:
    ranks = _ranks(g)
    lines = _order_ranks(g, ranks)
    w, h, dx, dy, top = 215, 48, 240, 92, 130
    max_per_row = max(len(nodes) for nodes in lines.values())
    width = max(80 + max_per_row * dx, 1080)
    height = top + (max(lines) + 1) * dy + 20
    pos: dict[str, tuple[float, float]] = {}
    for index, nodes in lines.items():
        start = (width - len(nodes) * dx) / 2 + (dx - w) / 2
        for i, n in enumerate(nodes):
            pos[n] = (start + i * dx, top + index * dy)

    o = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.0f} {height:.0f}" '
        f'width="{width:.0f}" height="{height:.0f}" font-family="Helvetica, Arial, sans-serif" '
        f'role="img" aria-label="{html.escape(title)}">',
        '<defs><marker id="s" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" '
        'orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#8A8A8A"/></marker></defs>',
        '<rect width="100%" height="100%" fill="#FFFFFF"/>',
        f'<text x="30" y="38" font-size="18" font-weight="bold" fill="#1F1F1F">{html.escape(title)}</text>',
    ]
    for i, layer in enumerate(LAYER_ORDER):
        background, border = COLORS[layer]
        x = 30 + i * 170
        o.append(
            f'<rect x="{x}" y="58" width="22" height="14" rx="3" fill="{background}" stroke="{border}"/>'
            f'<text x="{x + 30}" y="69" font-size="12" fill="#333">{LAYER_LABEL[layer]}</text>'
        )
    o.append(
        '<text x="30" y="96" font-size="11" fill="#555">Cylinder = stored table (bronze, ref). '
        "Box = model or consumer. Below the name: sharing · nature "
        "(data classification) and whether there is an ODCS contract.</text>"
    )
    for a, b in sorted(g.edges):
        (x1, y1), (x2, y2) = pos[a], pos[b]
        x1, x2, y1 = x1 + w / 2, x2 + w / 2, y1 + h
        c = (y2 - y1) / 2
        o.append(
            f'<path d="M{x1:.1f},{y1:.1f} C{x1:.1f},{y1 + c:.1f} {x2:.1f},{y2 - c:.1f} '
            f'{x2:.1f},{y2 - 2:.1f}" fill="none" stroke="#9A9A9A" stroke-width="1.3" marker-end="url(#s)"/>'
        )
    for uid, (x, y) in pos.items():
        o += _draw_node(g.nodes[uid], x, y, w, h)
    o.append("</svg>")
    return "\n".join(o)
