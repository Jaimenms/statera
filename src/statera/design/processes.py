"""Processes described in YAML (1:1 mirror of BPMN 2.0) <-> standard .bpmn files.

There is no established YAML standard for BPMN (see ADR 0007). So the YAML in this
repository is a direct mirror of the specification: element types use the BPMN 2.0 names
(`startEvent`, `userTask`, `exclusiveGateway`, `dataStoreReference`…), and whatever is specific to
the standard is isolated in the `statera` block. The output is always standard BPMN 2.0 XML, with DI
(coordinates), which opens in any tool.

    <unit>/2-design/process.yaml  --statera bpmn-->         process.bpmn (generated, alongside)
    <unit>/2-design/process.bpmn  --statera bpmn import-->  process.yaml
    (cross-cutting processes: governance/processes/<name>.yaml)

Flows leaving gateways carry `condition` (ok, failed, approved, rejected...), written to the
BPMN as conditionExpression: it is what the executor (statera.execution) uses to choose the path.

Automatic layout: columns by the longest path in the graph without the back flows; one lane
per row; data stores below the task that uses them; back flows routed underneath.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from xml.sax.saxutils import escape

import yaml
from jsonschema import Draft202012Validator

from statera import generated
from statera.design import bpmn
from statera.project import STATERA_SCHEMAS, current

COL, X0, Y0 = 175, 110, 80
LANE_HEIGHT, LANE_HEIGHT_WITH_DATA = 150, 230
SIZES = {
    "task": (100, 80),  # BPMN default size (bpmn.io, Camunda, Miragon lint)
    "event": (36, 36),
    "gateway": (50, 50),
    "dataStoreReference": (50, 50),
    "dataObjectReference": (36, 50),
}
ATTRIBUTES = ("type", "gsbpm", "durationMin", "implementation", "models", "exception", "awaits", "params", "checks")
Point = tuple[float, float]


def _shape(kind: str) -> str:
    if kind in bpmn.TASKS:
        return "task"
    if kind in bpmn.EVENTS:
        return "event"
    if kind in bpmn.GATEWAYS:
        return "gateway"
    return kind


# ---------------------------------------------------------------------------
# Reading and structural validation of the YAML
# ---------------------------------------------------------------------------


@cache
def _validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads((STATERA_SCHEMAS / "process.schema.json").read_text()))


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def sources() -> list[Path]:
    return current().processes()


def _flows(proc: dict) -> list[dict]:
    return [{"id": f.get("id") or f"Flow_{f['from']}__{f['to']}", **f} for f in proc["flows"]]


def validate_structure(proc: dict) -> list[str]:
    """Schema and reference errors (nonexistent ids, data without a task, duplicate ids)."""
    errors = [f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}" for e in _validator().iter_errors(proc)]
    if errors:
        return errors
    lanes = {r["id"] for r in proc["lanes"]}
    nodes = {e["id"] for e in proc["elements"]}
    data = {d["id"] for d in proc.get("data", [])}
    all_ = [r["id"] for r in proc["lanes"]] + [e["id"] for e in proc["elements"]] + list(data)
    errors += [f"duplicate id: {i}" for i in sorted({i for i in all_ if all_.count(i) > 1})]
    for e in proc["elements"]:
        if e["lane"] not in lanes:
            errors.append(f"{e['id']}: lane '{e['lane']}' does not exist")
        errors += [
            f"{e['id']}: data '{d}' does not exist" for d in e.get("inputs", []) + e.get("outputs", []) if d not in data
        ]
    for f in _flows(proc):
        errors += [f"flow {f['id']}: '{f[k]}' does not exist" for k in ("from", "to") if f[k] not in nodes]
    in_use = {d for e in proc["elements"] for d in e.get("inputs", []) + e.get("outputs", [])}
    errors += [f"data '{d}' is not associated with any task" for d in sorted(data - in_use)]
    if not any(e["type"] == "startEvent" for e in proc["elements"]):
        errors.append("process without a startEvent")
    return errors


# ---------------------------------------------------------------------------
# Automatic layout
# ---------------------------------------------------------------------------


@dataclass
class Layout:
    geo: dict[str, tuple[float, float, float, float]] = field(default_factory=dict)
    lanes: dict[str, tuple[float, float]] = field(default_factory=dict)  # id -> (top, height)
    width: float = 0
    routes: dict[str, list[Point]] = field(default_factory=dict)
    labels: dict[str, tuple[float, float, float, float]] = field(default_factory=dict)


def _loopbacks(proc: dict, flows: list[dict]) -> set[str]:
    """Back flows (edges to an ancestor in the depth-first search from the start)."""
    output: dict[str, list[dict]] = {}
    for f in flows:
        output.setdefault(f["from"], []).append(f)
    back_flows, visited, active_stack = set(), set(), set()

    def visit(node: str) -> None:
        visited.add(node)
        active_stack.add(node)
        for f in output.get(node, []):
            if f["to"] in active_stack:
                back_flows.add(f["id"])
            elif f["to"] not in visited:
                visit(f["to"])
        active_stack.discard(node)

    for e in proc["elements"]:
        if e["type"] == "startEvent" and e["id"] not in visited:
            visit(e["id"])
    return back_flows


def _columns(proc: dict, flows: list[dict], back_flows: set[str]) -> dict[str, int]:
    parents: dict[str, list[str]] = {e["id"]: [] for e in proc["elements"]}
    for f in flows:
        if f["id"] not in back_flows:
            parents[f["to"]].append(f["from"])
    lane = {e["id"]: e["lane"] for e in proc["elements"]}
    fixed = {e["id"]: e["column"] for e in proc["elements"] if "column" in e}
    column: dict[str, int] = {}
    occupied: set[tuple[str, int]] = set()
    pending_items = [e["id"] for e in proc["elements"]]
    while pending_items:  # stable topological order
        ready = [n for n in pending_items if all(p in column for p in parents[n])]
        if not ready:
            raise ValueError(f"cycle without an identifiable loopback among {pending_items}")
        for n in ready:
            c = fixed.get(n, max((column[p] + 1 for p in parents[n]), default=0))
            while (lane[n], c) in occupied:
                c += 1
            column[n] = c
            occupied.add((lane[n], c))
            pending_items.remove(n)
    return column


def _position(proc: dict, columns: dict[str, int], back_flow_targets: set[str]) -> Layout:
    lay = Layout()
    with_data = {e["lane"] for e in proc["elements"] if e.get("inputs") or e.get("outputs")}
    y = Y0
    for r in proc["lanes"]:
        height = LANE_HEIGHT_WITH_DATA if r["id"] in with_data else LANE_HEIGHT
        lay.lanes[r["id"]] = (y, height)
        y += height
    for e in proc["elements"]:
        w, h = SIZES[_shape(e["type"])]
        top, height = lay.lanes[e["lane"]]
        cy = top + (70 if e["lane"] in with_data else height / 2)
        cx = X0 + columns[e["id"]] * COL + 70
        lay.geo[e["id"]] = (cx - w / 2, cy - h / 2, w, h)
    data_types = {d["id"]: d["type"] for d in proc.get("data", [])}
    for e in proc["elements"]:
        associated = e.get("inputs", []) + e.get("outputs", [])
        for i, d in enumerate(associated):
            if d in lay.geo:
                continue
            w, h = SIZES[data_types[d]]
            tx, _, tw, _ = lay.geo[e["id"]]
            shift = -35 if e["id"] in back_flow_targets else 0  # frees the bottom for the back flow
            cx = tx + tw / 2 + shift + (i - (len(associated) - 1) / 2) * 60
            top, _ = lay.lanes[e["lane"]]
            lay.geo[d] = (cx - w / 2, top + 180 - h / 2, w, h)
    lay.width = X0 + (max(columns.values()) + 1) * COL + 40
    return lay


def _loopback_route(a: tuple, b: tuple, from_gateway: bool) -> list[Point]:
    """Flow back to an earlier element (a join gateway: flows never join on a task). An activity always
    exits to the right, with a straight stub, before turning (BPMN modeling conventions); a gateway may leave
    through a vertex. The flow passes underneath and enters the target through its bottom (or top, when the
    target is in a lane below)."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    acx, acy, bcx = ax + aw / 2, ay + ah / 2, bx + bw / 2
    stub = ax + aw + 25  # an activity's exit to the right
    if by > ay + ah:  # target in a lane below: comes from above it, entering through the top
        above = by - 20
        if from_gateway:
            return [(acx, ay + ah), (acx, above), (bcx, above), (bcx, by)]
        return [(ax + aw, acy), (stub, acy), (stub, above), (bcx, above), (bcx, by)]
    below = max(ay + ah, by + bh) + 22  # underneath the source and the target
    if from_gateway:
        return [(acx, ay + ah), (acx, below), (bcx, below), (bcx, by + bh)]
    return [(ax + aw, acy), (stub, acy), (stub, below), (bcx, below), (bcx, by + bh)]


def _forward_route(a: tuple, b: tuple, from_gateway: bool) -> list[Point]:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    acx, acy, bcy = ax + aw / 2, ay + ah / 2, by + bh / 2
    if abs(acy - bcy) < 1:
        return [(ax + aw, acy), (bx, bcy)]
    if from_gateway:  # gateway to another lane: leaves through the top or bottom vertex
        return [(acx, ay + ah if by > ay else ay), (acx, bcy), (bx, bcy)]
    if bx > ax + aw:
        middle = (ax + aw + bx) / 2
        return [(ax + aw, acy), (middle, acy), (middle, bcy), (bx, bcy)]
    bcx = bx + bw / 2
    return [(acx, ay + ah), (bcx, by)] if by > ay else [(acx, ay), (bcx, by + bh)]


def _route(lay: Layout, f: dict, backward: bool, gateways: set[str]) -> list[Point]:
    a, b = lay.geo[f["from"]], lay.geo[f["to"]]
    from_gateway = f["from"] in gateways
    return _loopback_route(a, b, from_gateway) if backward else _forward_route(a, b, from_gateway)


def _flow_label(points: list[Point]) -> tuple[float, float, float, float]:
    (x0, y0), (x1, y1) = points[0], points[1]
    if abs(x0 - x1) < 1:
        return (x0 + 6, y0 + 4 if y1 > y0 else y0 - 20, 30, 14)
    return (x0 + 6, y0 - 18, 30, 14)


def _node_label(box, occupied_sides: set[str]) -> tuple[float, float, float, float]:
    """Event or gateway label on the first side free of outgoing flows: bottom, top, right, left."""
    x, y, w, h = box
    if "bottom" not in occupied_sides:
        return (x + w / 2 - 50, y + h + 4, 100, 27)
    if "top" not in occupied_sides:
        return (x + w / 2 - 50, y - 31, 100, 27)
    if "right" not in occupied_sides:
        return (x + w + 4, y + h / 2 - 7, 70, 14)
    return (x - 105, y - 30, 100, 27)


def compute_layout(proc: dict) -> Layout:
    flows = _flows(proc)
    back_flows = _loopbacks(proc, flows)
    columns = _columns(proc, flows, back_flows)
    loopback_targets = {f["to"] for f in flows if f["id"] in back_flows}
    lay = _position(proc, columns, loopback_targets)
    gateways = {e["id"] for e in proc["elements"] if e["type"] in bpmn.GATEWAYS}
    for f in flows:
        lay.routes[f["id"]] = _route(lay, f, f["id"] in back_flows, gateways)
        if f.get("name"):
            lay.labels[f["id"]] = _flow_label(lay.routes[f["id"]])
    for e in proc["elements"]:
        if e["type"] in bpmn.TASKS:
            continue
        box = lay.geo[e["id"]]
        x, y, w, h = box
        start_points = [lay.routes[f["id"]][0] for f in flows if f["from"] == e["id"]]
        occupied_sides = set()
        for px, py in start_points:
            if abs(py - (y + h)) < 1:
                occupied_sides.add("bottom")
            elif abs(py - y) < 1:
                occupied_sides.add("top")
            elif abs(px - (x + w)) < 1:
                occupied_sides.add("right")
        lay.labels[e["id"]] = _node_label(box, occupied_sides)
    for d in proc.get("data", []):
        x, y, w, h = lay.geo[d["id"]]
        lay.labels[d["id"]] = (x + w / 2 - 50, y + h + 4, 100, 27)
    return lay


# ---------------------------------------------------------------------------
# BPMN 2.0 XML generation
# ---------------------------------------------------------------------------


def _ext_attributes(ext: dict) -> str:
    parts = []
    for key in ATTRIBUTES:
        if key not in ext:
            continue
        value = ext[key]
        if isinstance(value, dict):  # params: JSON in the attribute
            parts.append(f' statera:{key}="{escape(json.dumps(value, ensure_ascii=False), {chr(34): "&quot;"})}"')
            continue
        if isinstance(value, list):
            value = ",".join(value)
        elif isinstance(value, bool):
            value = str(value).lower()
        elif isinstance(value, float) and value.is_integer():
            value = int(value)
        parts.append(f' statera:{key}="{escape(str(value))}"')
    return "".join(parts)


def _xml_element(e: dict, flows: list[dict]) -> list[str]:
    name = f' name="{escape(e["name"])}"' if e.get("name") else ""
    lines = [f'    <bpmn:{e["type"]} id="{e["id"]}"{name}{_ext_attributes(e.get("statera", {}))}>']
    if e.get("documentation"):
        lines.append(f"      <bpmn:documentation>{escape(e['documentation'])}</bpmn:documentation>")
    lines += [f"      <bpmn:incoming>{f['id']}</bpmn:incoming>" for f in flows if f["to"] == e["id"]]
    lines += [f"      <bpmn:outgoing>{f['id']}</bpmn:outgoing>" for f in flows if f["from"] == e["id"]]
    if e.get("inputs"):
        lines.append(f'      <bpmn:property id="Property_{e["id"]}" name="__targetRef_placeholder" />')
    for i, d in enumerate(e.get("inputs", []), start=1):
        lines.append(
            f'      <bpmn:dataInputAssociation id="DataIn_{e["id"]}_{i}"><bpmn:sourceRef>{d}</bpmn:sourceRef>'
            f"<bpmn:targetRef>Property_{e['id']}</bpmn:targetRef></bpmn:dataInputAssociation>"
        )
    for i, d in enumerate(e.get("outputs", []), start=1):
        lines.append(
            f'      <bpmn:dataOutputAssociation id="DataOut_{e["id"]}_{i}">'
            f"<bpmn:targetRef>{d}</bpmn:targetRef></bpmn:dataOutputAssociation>"
        )
    if e.get("event"):
        lines.append(f'      <bpmn:{e["event"]}EventDefinition id="Def_{e["id"]}" />')
    lines.append(f"    </bpmn:{e['type']}>")
    return lines


def _xml_data(d: dict) -> list[str]:
    asset = f' statera:asset="{escape(d["asset"])}"' if d.get("asset") else ""
    name = escape(d["name"])
    if d["type"] == "dataObjectReference":
        return [
            f'    <bpmn:dataObject id="{d["id"]}_obj" />',
            f'    <bpmn:dataObjectReference id="{d["id"]}" name="{name}" dataObjectRef="{d["id"]}_obj"{asset} />',
        ]
    return [f'    <bpmn:dataStoreReference id="{d["id"]}" name="{name}"{asset} />']


def _bounds(box) -> str:
    x, y, w, h = box
    return f'<dc:Bounds x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" height="{h:.0f}" />'


def _xml_diagram(proc: dict, lay: Layout) -> list[str]:
    pid = proc["id"]
    total_height = sum(a for _, a in lay.lanes.values())
    lines = [
        f'  <bpmndi:BPMNDiagram id="Diagram_{pid}">',
        f'    <bpmndi:BPMNPlane id="Plane_{pid}" bpmnElement="Collab_{pid}">',
        f'      <bpmndi:BPMNShape id="Pool_{pid}_di" bpmnElement="Pool_{pid}" isHorizontal="true">',
        f"        {_bounds((20, Y0, lay.width, total_height))}",
        "      </bpmndi:BPMNShape>",
    ]
    for rid, (top, height) in lay.lanes.items():
        lines += [
            f'      <bpmndi:BPMNShape id="{rid}_di" bpmnElement="{rid}" isHorizontal="true">',
            f"        {_bounds((50, top, lay.width - 30, height))}",
            "      </bpmndi:BPMNShape>",
        ]
    gateways = {e["id"] for e in proc["elements"] if e["type"] in bpmn.GATEWAYS}
    for eid, box in lay.geo.items():
        marker = ' isMarkerVisible="true"' if eid in gateways else ""
        lines += [
            f'      <bpmndi:BPMNShape id="{eid}_di" bpmnElement="{eid}"{marker}>',
            f"        {_bounds(box)}",
        ]
        if eid in lay.labels:
            lines.append(f"        <bpmndi:BPMNLabel>{_bounds(lay.labels[eid])}</bpmndi:BPMNLabel>")
        lines.append("      </bpmndi:BPMNShape>")
    associations = []
    for e in proc["elements"]:
        _, ty, _, th = lay.geo[e["id"]]
        for i, d in enumerate(e.get("inputs", []), start=1):
            dx, dy, dw, _ = lay.geo[d]
            associations.append((f"DataIn_{e['id']}_{i}", [(dx + dw / 2, dy), (dx + dw / 2, ty + th)]))
        for i, d in enumerate(e.get("outputs", []), start=1):
            dx, dy, dw, _ = lay.geo[d]
            associations.append((f"DataOut_{e['id']}_{i}", [(dx + dw / 2, ty + th), (dx + dw / 2, dy)]))
    for fid, points in [*lay.routes.items(), *associations]:
        lines.append(f'      <bpmndi:BPMNEdge id="{fid}_di" bpmnElement="{fid}">')
        lines += [f'        <di:waypoint x="{x:.0f}" y="{y:.0f}" />' for x, y in points]
        if fid in lay.labels:
            lines.append(f"        <bpmndi:BPMNLabel>{_bounds(lay.labels[fid])}</bpmndi:BPMNLabel>")
        lines.append("      </bpmndi:BPMNEdge>")
    return [*lines, "    </bpmndi:BPMNPlane>", "  </bpmndi:BPMNDiagram>"]


def generate_xml(proc: dict, origin: str) -> str:
    errors = validate_structure(proc)
    if errors:
        raise ValueError(f"{origin}: invalid process:\n  - " + "\n  - ".join(errors))
    pid, flows, lay = proc["id"], _flows(proc), compute_layout(proc)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        generated.header(
            "statera bpmn",
            f"{origin} (the process in YAML, mirror of the BPMN)",
            style="xml",
            note="To draw: open this .bpmn in Camunda Modeler or bpmn.io and run\n"
            "`statera bpmn import <this file>` to bring the change to the YAML.",
        ).rstrip("\n"),
        '<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" '
        'xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" '
        'xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" xmlns:di="http://www.omg.org/spec/DD/20100524/DI" '
        f'xmlns:xsi="{bpmn.NS["xsi"]}" xmlns:statera="{bpmn.NS["statera"]}" id="Definitions_{pid}" '
        'targetNamespace="urn:statera:processes" exporter="statera (statera bpmn)" exporterVersion="1.0">',
        f'  <bpmn:collaboration id="Collab_{pid}">',
        f'    <bpmn:participant id="Pool_{pid}" name="{escape(proc["name"])}" processRef="{pid}" />',
        "  </bpmn:collaboration>",
        f'  <bpmn:process id="{pid}" isExecutable="false">',
    ]
    if proc.get("description"):
        lines.append(f"    <bpmn:documentation>{escape(proc['description'])}</bpmn:documentation>")
    lines.append(f'    <bpmn:laneSet id="LaneSet_{pid}">')
    for r in proc["lanes"]:
        lines.append(f'      <bpmn:lane id="{r["id"]}" name="{escape(r["name"])}" statera:role="{r["role"]}">')
        lines += [
            f"        <bpmn:flowNodeRef>{e['id']}</bpmn:flowNodeRef>" for e in proc["elements"] if e["lane"] == r["id"]
        ]
        lines.append("      </bpmn:lane>")
    lines.append("    </bpmn:laneSet>")
    for e in proc["elements"]:
        lines += _xml_element(e, flows)
    for f in flows:
        name = f' name="{escape(f["name"])}"' if f.get("name") else ""
        open_tag = f'    <bpmn:sequenceFlow id="{f["id"]}" sourceRef="{f["from"]}" targetRef="{f["to"]}"{name}'
        if not f.get("condition"):
            lines.append(open_tag + " />")
            continue
        lines += [
            open_tag + ">",
            '      <bpmn:conditionExpression xsi:type="bpmn:tFormalExpression">'
            f"${{status == '{escape(f['condition'])}'}}</bpmn:conditionExpression>",
            "    </bpmn:sequenceFlow>",
        ]
    for d in proc.get("data", []):
        lines += _xml_data(d)
    lines.append("  </bpmn:process>")
    lines += _xml_diagram(proc, lay)
    lines.append("</bpmn:definitions>")
    return "\n".join(lines) + "\n"


def bpmn_destination(source: Path) -> Path:
    return source.with_suffix(".bpmn")


def sync() -> list[Path]:
    generated_files = []
    for source in sources():
        destination = bpmn_destination(source)
        destination.write_text(generate_xml(load(source), source.name), encoding="utf-8")
        generated_files.append(destination)
    return generated_files


def outdated() -> list[Path]:
    """YAML sources whose .bpmn does not exist or differs from what would be generated."""
    output = []
    for source in sources():
        destination = bpmn_destination(source)
        expected = generate_xml(load(source), source.name)
        if not destination.exists() or destination.read_text(encoding="utf-8") != expected:
            output.append(source)
    return output


def bpmn_without_source() -> list[Path]:
    project = current()
    folders = {u.phase(2) for u in project.units} | {u.legacy_processes_folder for u in project.units}
    folders |= {project.governance / "processes"}
    return [
        b for folder in sorted(folders) for b in sorted(folder.glob("*.bpmn")) if not b.with_suffix(".yaml").exists()
    ]


# ---------------------------------------------------------------------------
# Import: .bpmn (edited in a visual tool) -> YAML
# ---------------------------------------------------------------------------


def _ext_from_attrs(attrs: dict) -> dict:
    ext = {}
    for key in ATTRIBUTES:
        if key not in attrs:
            continue
        value = attrs[key]
        if key == "durationMin":
            value = float(value) if "." in value else int(value)
        elif key in ("models", "checks"):
            value = [m for m in value.split(",") if m]
        elif key == "params":
            value = json.loads(value)
        elif key == "exception":
            value = value == "true"
        ext[key] = value
    return ext


def to_dict(path: Path) -> dict:
    p = bpmn.read(path)
    root = ET.parse(path).getroot()
    process = root.find("bpmn:process", bpmn.NS)
    proc: dict = {"id": process.get("id"), "name": p.name}
    doc = process.find("bpmn:documentation", bpmn.NS)
    if doc is not None and doc.text:
        proc["description"] = doc.text.strip()
    proc["lanes"] = [{"id": lid, "name": r["name"], "role": r["role"]} for lid, r in p.lanes.items()]
    elements, data = [], []
    for el in p.elements.values():
        if el.tag in ("dataStoreReference", "dataObjectReference"):
            data.append(
                {"id": el.id, "type": el.tag, "name": el.name}
                | ({"asset": el.attrs["asset"]} if "asset" in el.attrs else {})
            )
            continue
        item: dict = {"id": el.id, "type": el.tag, **({"name": el.name} if el.name else {}), "lane": el.lane}
        if el.timer:
            item["event"] = "timer"
        elif el.message:
            item["event"] = "message"
        if el.documentation:
            item["documentation"] = el.documentation
        inputs = [f["from"] for f in p.flows.values() if f["tag"] == "dataInput" and f["to"] == el.id]
        outputs = [f["to"] for f in p.flows.values() if f["tag"] == "dataOutput" and f["from"] == el.id]
        if inputs:
            item["inputs"] = inputs
        if outputs:
            item["outputs"] = outputs
        if ext := _ext_from_attrs(el.attrs):
            item["statera"] = ext
        elements.append(item)
    proc["elements"] = elements
    if data:
        proc["data"] = data
    proc["flows"] = [
        {"id": fid, "from": f["from"], "to": f["to"]}
        | ({"name": f["name"]} if f["name"] else {})
        | ({"condition": f["condition"]} if f.get("condition") else {})
        for fid, f in p.flows.items()
        if f["tag"] == "sequenceFlow"
    ]
    return proc


def _inline(value) -> str:
    text = yaml.safe_dump(value, default_flow_style=True, allow_unicode=True, sort_keys=False, width=10_000)
    return text.strip().removesuffix("...").strip()  # scalars come out with an end-of-document marker


INLINE_WIDTH = 100


def _yaml_value(key: str, value, indent: str) -> list[str]:
    """`key: value` inline when it fits; otherwise as a block (long lists and mappings)."""
    inline = _inline(value)
    if not isinstance(value, list | dict) or len(indent) + len(key) + len(inline) + 2 <= INLINE_WIDTH:
        return [f"{indent}{key}: {inline}"]
    lines = [f"{indent}{key}:"]
    if isinstance(value, list):
        return lines + [f"{indent}  - {_inline(item)}" for item in value]
    for k, v in value.items():
        lines += _yaml_value(k, v, indent + "  ")
    return lines


def _element_yaml(e: dict) -> list[str]:
    inline = _inline(e)
    if len(inline) + 4 <= INLINE_WIDTH:
        return [f"  - {inline}"]
    lines = []
    for i, (k, v) in enumerate(e.items()):
        block = _yaml_value(k, v, "    ")
        if i == 0:
            block[0] = "  - " + block[0].lstrip()
        lines += block
    return lines


def to_yaml(proc: dict, origin: str = "") -> str:
    header = [
        "# Process in YAML, 1:1 mirror of BPMN 2.0 (schema: statera process.schema.json).",
        "# Source of the .bpmn with the same name, generated by `statera bpmn`."
        + (f" Imported from {origin}." if origin else ""),
    ]
    top = {k: proc[k] for k in ("id", "name", "description") if k in proc}
    body = yaml.safe_dump(top, allow_unicode=True, sort_keys=False, width=110).rstrip()
    parts = [*header, body, "", "lanes:"]
    parts += [f"  - {_inline(r)}" for r in proc["lanes"]]
    parts += ["", "elements:"]
    for e in proc["elements"]:
        parts += _element_yaml(e)
    if proc.get("data"):
        parts += ["", "data:"] + [f"  - {_inline(d)}" for d in proc["data"]]
    parts += ["", "flows:"] + [f"  - {_inline(f)}" for f in proc["flows"]]
    return "\n".join(parts) + "\n"


def import_bpmn(path: Path, destination: Path | None = None) -> Path:
    destination = destination or path.with_suffix(".yaml")
    destination.write_text(to_yaml(to_dict(path), path.name), encoding="utf-8")
    return destination
