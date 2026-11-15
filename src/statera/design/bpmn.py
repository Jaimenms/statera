"""Reading, validation and visualization of BPMN 2.0 processes.

The .bpmn files (<unit>/2-design/process.bpmn) are generated from the process YAML files
(processes.py) and open in any BPMN tool (Camunda Modeler, bpmn.io).
Attributes in the `statera` namespace link each task to the repository and to the run:

    statera:type            automatic | semi-automatic | manual
    statera:gsbpm           GSBPM sub-process (e.g. 5.7, 6.4;7.2, 5.1-5.7)
    statera:durationMin     expected duration per edition, in minutes
    statera:implementation  py:<module>.<function> | dbt:build
    statera:models          dbt models to build (with everything upstream) in dbt:build
    statera:exception       "true" for tasks off the normal path (not counted in the effort)
    statera:awaits          (on an intermediate event) function that says whether the wait is over
    statera:role            (on the lane) role from governance/roles.yaml

On flows leaving gateways, `conditionExpression` (${status == 'ok'}) tells which path the
executor follows (statera.execution).

This module:
  - renders SVG from the file's own DI (no Node or browser), to be displayed
    on GitHub and in the VS Code Markdown preview;
  - generates an interactive HTML viewer with bpmn-js;
  - summarizes tasks, times and types (effort per edition);
  - validates the links between the diagram and the repository.
"""

from __future__ import annotations

import html
import importlib
import re
import textwrap
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from statera import generated
from statera.project import current

NS = {
    "bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL",
    "bpmndi": "http://www.omg.org/spec/BPMN/20100524/DI",
    "dc": "http://www.omg.org/spec/DD/20100524/DC",
    "di": "http://www.omg.org/spec/DD/20100524/DI",
    "statera": "urn:statera:bpmn:1.0",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}
EXT = f"{{{NS['statera']}}}"
TASKS = {
    "task",
    "userTask",
    "serviceTask",
    "manualTask",
    "scriptTask",
    "sendTask",
    "receiveTask",
    "businessRuleTask",
    "callActivity",
    "subProcess",
}
EVENTS = {"startEvent", "endEvent", "intermediateCatchEvent", "intermediateThrowEvent", "boundaryEvent"}
GATEWAYS = {"exclusiveGateway", "parallelGateway", "inclusiveGateway", "eventBasedGateway"}
TYPE_COLORS = {
    "automatic": ("#E3F4E8", "#2E7D4F"),
    "semi-automatic": ("#FFF4D6", "#A6750A"),
    "manual": ("#FDE4E1", "#B03A2E"),
    None: ("#FFFFFF", "#404040"),
}
FONT = 'font-family="Helvetica, Arial, sans-serif"'
BPMN_JS_VERSION = "18.30.1"
Box = tuple[float, float, float, float]


@dataclass
class Element:
    id: str
    tag: str
    name: str
    attrs: dict[str, str] = field(default_factory=dict)
    lane: str | None = None
    timer: bool = False
    documentation: str = ""
    message: bool = False

    @property
    def kind(self) -> str | None:
        return self.attrs.get("type")

    @property
    def duration(self) -> float:
        return float(self.attrs.get("durationMin", 0) or 0)


@dataclass
class Process:
    path: Path
    name: str
    elements: dict[str, Element]
    lanes: dict[str, dict]
    shapes: dict[str, Box]
    labels: dict[str, Box]
    edges: dict[str, list[tuple[float, float]]]
    flows: dict[str, dict]

    @property
    def tasks(self) -> list[Element]:
        return [e for e in self.elements.values() if e.tag in TASKS]


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def _local(tag: str) -> str:
    return tag.split("}", 1)[-1]


def _box(node: ET.Element | None) -> Box | None:
    if node is None:
        return None
    return tuple(float(node.get(k)) for k in ("x", "y", "width", "height"))  # type: ignore[return-value]


CONDITION = re.compile(r"status\s*==\s*'([\w-]+)'")


def _condition(flow: ET.Element) -> str:
    """Flow condition, from <conditionExpression>${status == 'ok'}</conditionExpression>."""
    expression = flow.find("bpmn:conditionExpression", NS)
    finding = CONDITION.search(expression.text or "") if expression is not None else None
    return finding.group(1) if finding else ""


def _read_lanes(process: ET.Element) -> dict[str, dict]:
    lanes = {}
    for lane in process.iter(f"{{{NS['bpmn']}}}lane"):
        lanes[lane.get("id")] = {
            "name": lane.get("name", ""),
            "role": lane.get(f"{EXT}role"),
            "nodes": [r.text for r in lane.findall("bpmn:flowNodeRef", NS)],
        }
    return lanes


def _read_associations(node: ET.Element, flows: dict[str, dict]) -> None:
    for assoc in node.findall("bpmn:dataInputAssociation", NS):
        origin = assoc.find("bpmn:sourceRef", NS).text
        flows[assoc.get("id")] = {"tag": "dataInput", "from": origin, "to": node.get("id"), "name": ""}
    for assoc in node.findall("bpmn:dataOutputAssociation", NS):
        destination = assoc.find("bpmn:targetRef", NS).text
        flows[assoc.get("id")] = {"tag": "dataOutput", "from": node.get("id"), "to": destination, "name": ""}


def _read_elements(process: ET.Element, elements: dict, flows: dict) -> None:
    for child in process:
        tag = _local(child.tag)
        if tag in ("laneSet", "dataObject"):
            continue
        if tag in ("sequenceFlow", "association"):
            flows[child.get("id")] = {
                "tag": tag,
                "from": child.get("sourceRef"),
                "to": child.get("targetRef"),
                "name": child.get("name", ""),
                "condition": _condition(child),
            }
            continue
        doc = child.find("bpmn:documentation", NS)
        elements[child.get("id")] = Element(
            id=child.get("id"),
            tag=tag,
            name=child.get("name", ""),
            attrs={k[len(EXT) :]: v for k, v in child.attrib.items() if k.startswith(EXT)},
            timer=child.find("bpmn:timerEventDefinition", NS) is not None,
            message=child.find("bpmn:messageEventDefinition", NS) is not None,
            documentation=(doc.text or "").strip() if doc is not None else "",
        )
        _read_associations(child, flows)


def read(path: Path) -> Process:
    root = ET.parse(path).getroot()
    participant = root.find(".//bpmn:participant", NS)
    name = participant.get("name") if participant is not None and participant.get("name") else path.stem
    elements: dict[str, Element] = {}
    lanes: dict[str, dict] = {}
    flows: dict[str, dict] = {}
    for process in root.findall("bpmn:process", NS):
        lanes |= _read_lanes(process)
        _read_elements(process, elements, flows)
    for lid, lane in lanes.items():
        for ref in lane["nodes"]:
            if ref in elements:
                elements[ref].lane = lid

    shapes, labels, edges = {}, {}, {}
    for shape in root.iter(f"{{{NS['bpmndi']}}}BPMNShape"):
        shapes[shape.get("bpmnElement")] = _box(shape.find("dc:Bounds", NS))
        if label_box := _box(shape.find("bpmndi:BPMNLabel/dc:Bounds", NS)):
            labels[shape.get("bpmnElement")] = label_box
    for edge in root.iter(f"{{{NS['bpmndi']}}}BPMNEdge"):
        points = [(float(w.get("x")), float(w.get("y"))) for w in edge.findall("di:waypoint", NS)]
        edges[edge.get("bpmnElement")] = points
        if label_box := _box(edge.find("bpmndi:BPMNLabel/dc:Bounds", NS)):
            labels[edge.get("bpmnElement")] = label_box
    return Process(path, name, elements, lanes, shapes, labels, edges, flows)


def all_() -> list[Process]:
    return [read(c.with_suffix(".bpmn")) for c in current().processes() if c.with_suffix(".bpmn").exists()]


def summary(p: Process) -> dict:
    """Hours per edition on the normal path (tasks with statera:exception="true" are left out)."""
    hours = {"manual": 0.0, "semi-automatic": 0.0, "automatic": 0.0}
    for t in p.tasks:
        if t.kind in hours and t.attrs.get("exception") != "true":
            hours[t.kind] += t.duration / 60
    return {
        "tasks": len(p.tasks),
        **{f"{k}_h": v for k, v in hours.items()},
        "total_h": sum(hours.values()),
    }


# ---------------------------------------------------------------------------
# SVG
# ---------------------------------------------------------------------------


def _text(
    x: float,
    y: float,
    content: str,
    width_chars: int,
    size: float = 11,
    anchor: str = "middle",
    weight: str = "normal",
    color: str = "#1F1F1F",
    vertical: str = "center",
) -> str:
    lines = textwrap.wrap(content, width_chars) or [""]
    height = size * 1.2
    y0 = y - (len(lines) - 1) * height / 2 if vertical == "center" else y
    spans = "".join(
        f'<tspan x="{x:.1f}" y="{y0 + i * height:.1f}">{html.escape(t)}</tspan>' for i, t in enumerate(lines)
    )
    return (
        f'<text {FONT} font-size="{size}" font-weight="{weight}" fill="{color}" '
        f'text-anchor="{anchor}" dominant-baseline="middle">{spans}</text>'
    )


def _vertical_text(x: float, y: float, content: str, max_chars: int, size: float, weight: str) -> list[str]:
    lines = textwrap.wrap(content, max(8, max_chars))[:2]
    output = []
    for i, line in enumerate(lines):
        dx = 15 + (i - (len(lines) - 1) / 2) * (size + 2)
        output.append(
            f'<text transform="translate({x + dx + 3},{y}) rotate(-90)" text-anchor="middle" '
            f'dominant-baseline="middle" font-weight="{weight}" {FONT} font-size="{size}">'
            f"{html.escape(line)}</text>"
        )
    return output


def _arrow(points: list[tuple[float, float]], is_data: bool) -> str:
    d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in points)
    if is_data:
        style = 'stroke="#666" stroke-dasharray="4 3" marker-end="url(#arrow-data)"'
    else:
        style = 'stroke="#333" marker-end="url(#arrow)"'
    return f'<path d="{d}" fill="none" stroke-width="1.4" {style}/>'


def _task_icon(tag: str, x: float, y: float) -> str:
    if tag == "serviceTask":  # simplified gear
        return (
            f'<circle cx="{x + 12}" cy="{y + 12}" r="6" fill="none" stroke="#2E7D4F" stroke-width="2" '
            f'stroke-dasharray="3 1.6"/><circle cx="{x + 12}" cy="{y + 12}" r="2" fill="#2E7D4F"/>'
        )
    if tag == "userTask":  # person
        return (
            f'<circle cx="{x + 12}" cy="{y + 9}" r="3.5" fill="none" stroke="#444" stroke-width="1.5"/>'
            f'<path d="M{x + 5},{y + 19} q7,-9 14,0" fill="none" stroke="#444" stroke-width="1.5"/>'
        )
    if tag == "manualTask":  # hand (simplified)
        return (
            f'<path d="M{x + 5},{y + 16} h8 v-8 M{x + 9},{y + 16} v-9 M{x + 13},{y + 16} v-8" '
            f'fill="none" stroke="#B03A2E" stroke-width="1.5"/>'
        )
    return ""


def _draw_task(el: Element, box: Box) -> list[str]:
    x, y, w, h = box
    background, border = TYPE_COLORS.get(el.kind, TYPE_COLORS[None])
    parts = [
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{background}" '
        f'stroke="{border}" stroke-width="1.6"/>',
        _task_icon(el.tag, x, y),
        _text(x + w / 2, y + h / 2 + 2, el.name, 22, 10.5),
    ]
    if el.attrs.get("gsbpm"):
        parts.append(_text(x + w - 5, y + 10, f"GSBPM {el.attrs['gsbpm']}", 20, 8.5, "end", color="#555"))
    if el.duration:
        dur = f"{el.duration / 60:.1f} h" if el.duration >= 60 else f"{el.duration:.0f} min"
        parts.append(_text(x + w - 5, y + h - 8, f"~{dur}", 12, 8.5, "end", "bold", border))
    return parts


def _draw_event(el: Element, box: Box) -> list[str]:
    x, y, w, h = box
    cx, cy, r = x + w / 2, y + h / 2, w / 2
    stroke_width = 3.5 if el.tag == "endEvent" else 1.6
    parts = [f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="#FFF" stroke="#333" stroke-width="{stroke_width}"/>']
    if el.tag.startswith("intermediate"):
        parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r - 3}" fill="none" stroke="#333" stroke-width="1.2"/>')
    if el.timer:
        parts.append(
            f'<circle cx="{cx}" cy="{cy}" r="{r - 6}" fill="none" stroke="#333"/>'
            f'<path d="M{cx},{cy - r + 9} L{cx},{cy} L{cx + 6},{cy}" stroke="#333" fill="none"/>'
        )
    if el.message:  # envelope
        parts.append(
            f'<rect x="{cx - 8}" y="{cy - 5.5}" width="16" height="11" fill="none" stroke="#333"/>'
            f'<path d="M{cx - 8},{cy - 5.5} L{cx},{cy + 1} L{cx + 8},{cy - 5.5}" fill="none" stroke="#333"/>'
        )
    return parts


def _draw_gateway(el: Element, box: Box) -> list[str]:
    x, y, w, h = box
    cx, cy = x + w / 2, y + h / 2
    points = f"{cx},{y} {x + w},{cy} {cx},{y + h} {x},{cy}"
    parts = [f'<polygon points="{points}" fill="#FFF" stroke="#333" stroke-width="1.6"/>']
    if el.tag == "exclusiveGateway":
        mark = f"M{cx - 8},{cy - 8} L{cx + 8},{cy + 8} M{cx + 8},{cy - 8} L{cx - 8},{cy + 8}"
        parts.append(f'<path d="{mark}" stroke="#333" stroke-width="3"/>')
    elif el.tag == "parallelGateway":
        mark = f"M{cx},{cy - 11} V{cy + 11} M{cx - 11},{cy} H{cx + 11}"
        parts.append(f'<path d="{mark}" stroke="#333" stroke-width="3"/>')
    return parts


def _draw_data(el: Element, box: Box) -> list[str]:
    x, y, w, h = box
    style = 'fill="#F3F6FB" stroke="#4A6FA5" stroke-width="1.4"'
    if el.tag == "dataStoreReference":
        arc = f"a{w / 2},8 0 0 0"
        d = f"M{x},{y + 8} v{h - 16} {arc} {w},0 v-{h - 16} {arc} -{w},0 {arc} {w},0"
    else:
        d = f"M{x},{y} h{w - 10} l10,10 v{h - 10} h-{w} z"
    return [f'<path d="{d}" {style}/>']


def _draw_element(p: Process, eid: str, el: Element) -> list[str]:
    box = p.shapes[eid]
    if el.tag in TASKS:
        return _draw_task(el, box)
    if el.tag in EVENTS:
        parts = _draw_event(el, box)
    elif el.tag in GATEWAYS:
        parts = _draw_gateway(el, box)
    elif el.tag in ("dataStoreReference", "dataObjectReference"):
        parts = _draw_data(el, box)
    else:
        parts = []
    if el.name:
        x, y, w, h = box
        rx, ry, rw, _ = p.labels.get(eid, (x + w / 2 - 50, y + h + 4, 100, 27))
        parts.append(_text(rx + rw / 2, ry + 6, el.name, 22, 10, vertical="top"))
    return parts


def _draw_pool_and_lanes(p: Process) -> list[str]:
    parts = []
    for eid, (x, y, w, h) in p.shapes.items():
        if eid.startswith("Pool"):
            parts.append(
                f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="#FAFAFA" stroke="#333" '
                f'stroke-width="1.5"/><line x1="{x + 30}" y1="{y}" x2="{x + 30}" y2="{y + h}" stroke="#333"/>'
            )
            parts += _vertical_text(x, y + h / 2, p.name, int(h / 7.8), 11.5, "bold")
    for lid, lane in p.lanes.items():
        if lid in p.shapes:
            x, y, w, h = p.shapes[lid]
            parts.append(
                f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="none" stroke="#999"/>'
                f'<line x1="{x + 30}" y1="{y}" x2="{x + 30}" y2="{y + h}" stroke="#999"/>'
            )
            parts += _vertical_text(x, y + h / 2, lane["name"], int(h / 6.4), 10, "normal")
    return parts


def _draw_edges(p: Process) -> list[str]:
    parts = []
    for fid, points in p.edges.items():
        flow = p.flows.get(fid, {})
        parts.append(_arrow(points, flow.get("tag") in ("dataInput", "dataOutput", "association")))
        if flow.get("name") and fid in p.labels:
            x, y, w, h = p.labels[fid]
            parts.append(_text(x + w / 2, y + h / 2, flow["name"], 12, 10.5, weight="bold", color="#333"))
    return parts


def _draw_legend(p: Process, y: float) -> list[str]:
    parts = [_text(20, y, "Legend:", 20, 11, "start", "bold", vertical="top")]
    kinds = [("automatic", "automatic"), ("semi-automatic", "semi-automatic"), ("manual", "manual")]
    for i, (kind, label) in enumerate(kinds):
        background, border = TYPE_COLORS[kind]
        x = 90 + i * 150
        parts.append(f'<rect x="{x}" y="{y - 7}" width="26" height="16" rx="4" fill="{background}" stroke="{border}"/>')
        parts.append(_text(x + 34, y + 1, label, 20, 11, "start"))
    r = summary(p)
    text = (
        f"Work time per edition: {r['manual_h']:.1f} h manual, {r['semi-automatic_h']:.1f} h "
        f"semi-automatic, {r['automatic_h']:.1f} h automatic (modeled durations, normal path)."
    )
    parts.append(_text(20, y + 26, text, 200, 11, "start", vertical="top"))
    return parts


def to_svg(p: Process) -> str:
    xs = [x + w for x, _, w, _ in p.shapes.values()] + [x for pts in p.edges.values() for x, _ in pts]
    ys = [y + h for _, y, _, h in p.shapes.values()] + [y for pts in p.edges.values() for _, y in pts]
    width, height = max(xs) + 30, max(ys) + 110
    markers = (
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
        'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#333"/></marker>'
        '<marker id="arrow-data" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
        'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10" fill="none" stroke="#666"/></marker></defs>'
    )
    o = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.0f} {height:.0f}" '
        f'width="{width:.0f}" height="{height:.0f}" role="img" aria-label="{html.escape(p.name)}">',
        markers,
        '<rect width="100%" height="100%" fill="#FFFFFF"/>',
        _text(20, 40, p.name, 140, 16, "start", "bold", vertical="top"),
        *_draw_pool_and_lanes(p),
        *_draw_edges(p),
    ]
    for eid, el in p.elements.items():
        if eid in p.shapes:
            o += _draw_element(p, eid, el)
    o += _draw_legend(p, height - 60)
    o.append("</svg>")
    return "\n".join(o)


def viewer_html(p: Process) -> str:
    xml = p.path.read_text(encoding="utf-8").replace("</script", "<\\/script")
    cdn = f"https://unpkg.com/bpmn-js@{BPMN_JS_VERSION}/dist"
    origin = html.escape(current().rel(p.path))
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(p.name)}</title>
<link rel="stylesheet" href="{cdn}/assets/diagram-js.css">
<link rel="stylesheet" href="{cdn}/assets/bpmn-js.css">
<style>html,body{{margin:0;height:100%;font-family:Helvetica,Arial,sans-serif;background:#fff}}
#top{{padding:8px 16px;border-bottom:1px solid #ddd}} #canvas{{height:calc(100% - 50px)}}</style>
</head>
<body>
<div id="top"><strong>{html.escape(p.name)}</strong> — drag to move, scroll to zoom.
Source: <code>{origin}</code></div>
<div id="canvas"></div>
<script src="{cdn}/bpmn-navigated-viewer.production.min.js"></script>
<script id="diagram" type="application/xml">{xml}</script>
<script>
  const viewer = new BpmnJS({{ container: '#canvas' }});
  viewer.importXML(document.getElementById('diagram').textContent)
    .then(() => viewer.get('canvas').zoom('fit-viewport'))
    .catch(err => document.getElementById('canvas').innerText = 'Error: ' + err.message);
</script>
</body>
</html>
"""


def svg_destination(p: Process) -> Path:
    project = current()
    unit = project.unit_of(p.path)
    name = unit.name if unit else f"governance__{p.path.stem}"  # one process per product
    return project.docs / "processes" / f"{name}.svg"


def render_all() -> list[Path]:
    generated_files = []
    for p in all_():
        svg = svg_destination(p)
        svg.parent.mkdir(parents=True, exist_ok=True)
        notice = generated.header(
            "statera bpmn", current().rel(p.path), regenerate="statera bpmn", ci=False, style="xml"
        )
        svg.write_text(notice + to_svg(p), encoding="utf-8")
        viewer = svg.with_suffix(".html")
        viewer.write_text(generated.as_html(viewer_html(p), notice), encoding="utf-8")
        generated_files += [svg, viewer]
    return generated_files


# ---------------------------------------------------------------------------
# Validation of the BPMN -> repository links
# ---------------------------------------------------------------------------

GSBPM_PATTERN = re.compile(r"^\d\.\d+([-;]\d\.\d+)*$")
# dbt selector graph operators: +model (ancestors), model+ (descendants), @model, 2+model
DBT_OPERATORS = re.compile(r"^(?:@|\d*\+)?(?P<name>[^+@]+?)(?:\+\d*)?$")


def selector_name(selector: str) -> str:
    """'+int_x' -> 'int_x' (strips the dbt selector graph operators)."""
    finding = DBT_OPERATORS.match(selector.strip())
    return finding.group("name") if finding else selector.strip()


def _validate_implementation(name: str, t: Element, dbt_models: set[str]) -> list[str]:
    impl = t.attrs.get("implementation")
    if not impl:
        if t.kind == "automatic":
            return [f"{name}: {t.id} is automatic but does not declare statera:implementation"]
        return []
    if impl.startswith("py:"):
        module, _, function = impl[3:].rpartition(".")
        try:
            exists = hasattr(importlib.import_module(module), function)
        except ImportError:
            return [f"{name}: {t.id} points to nonexistent module {module}"]
        return [] if exists else [f"{name}: {t.id} points to {impl}, which does not exist"]
    if impl.startswith("dbt:"):
        target = impl[4:]
        entries = t.attrs.get("models", "").split(",") if target == "build" else [target]
        return [
            f"{name}: {t.id} points to nonexistent dbt model '{m}'"
            for m in entries
            if m.strip() and selector_name(m) not in dbt_models
        ]
    return []  # a class name: resolved and checked by statera.extend.checks (validation group "processes")


def validate(p: Process, dbt_models: set[str], roles: set[str]) -> list[str]:
    name = current().rel(p.path)
    errors = [
        f"{name}: lane {lid} has unknown role '{lane['role']}'"
        for lid, lane in p.lanes.items()
        if lane["role"] not in roles
    ]
    for el in p.elements.values():
        if (awaits := el.attrs.get("awaits")) and el.tag not in TASKS and awaits.startswith("py:"):
            module, _, function = awaits[3:].rpartition(".")
            try:
                exists = hasattr(importlib.import_module(module), function)
            except ImportError:
                exists = False
            if not exists:
                errors.append(f"{name}: {el.id} awaits {awaits}, which does not exist")
    for t in p.tasks:
        if t.kind not in ("automatic", "semi-automatic", "manual"):
            errors.append(f"{name}: {t.id} has no valid statera:type")
        if not GSBPM_PATTERN.match(t.attrs.get("gsbpm", "")):
            errors.append(f"{name}: {t.id} has no valid statera:gsbpm")
        if not t.duration:
            errors.append(f"{name}: {t.id} has no statera:durationMin")
        errors += _validate_implementation(name, t, dbt_models)
    errors += [f"{name}: {eid} has no shape in the diagram (DI)" for eid in p.elements if eid not in p.shapes]
    return errors
