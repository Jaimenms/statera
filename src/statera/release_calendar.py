"""Calendar of internal products: the cuts their consumers request (specification 09).

An internal product has no calendar of its own: it delivers the cuts that the calendars of the
dissemination products request, directly or through other internal products (source system →
canonical entities → dissemination product). The `1-specify/calendar.yaml` file of each internal product is GENERATED
from those calendars, so that the cut-off date has a single source: the calendar of whoever disseminates.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from statera import generated, ports
from statera.project import Project, Unit, current

RELATIVE_PATH = "1-specify/calendar.yaml"


def consumers(project: Project, unit: Unit) -> list[Unit]:
    return [u for u in project.products() if unit.name in {e.name for e in ports.input_products(u)}]


def requested_cuts(project: Project, unit: Unit, _seen: frozenset = frozenset()) -> dict[str, list[str]]:
    """{cut-off date: editions that request it}, walking up the consumers to the dissemination products."""
    output: dict[str, set[str]] = {}
    for c in consumers(project, unit):
        if c.name in _seen:
            continue
        if c.internal:
            for cut_date, who in requested_cuts(project, c, _seen | {unit.name}).items():
                output.setdefault(cut_date, set()).update(who)
        else:
            for e in (c.read(RELATIVE_PATH) or {}).get("editions", []):
                output.setdefault(str(e["cut_date"]), set()).add(f"{c.name} {e['period']}")
    return {cut_date: sorted(who) for cut_date, who in sorted(output.items())}


def generate(project: Project, unit: Unit) -> str:
    content = {
        "product": unit.name,
        "cut_date_rule": "Cuts requested by the calendars of the products that read this product",
        "cuts": [{"cut_date": d, "requested_by": q} for d, q in requested_cuts(project, unit).items()],
    }
    notice = generated.header(
        "statera sync",
        "the calendars (1-specify/calendar.yaml) of the dissemination products that read this product",
        note="Internal product: it has no calendar of its own; it delivers the cuts its consumers request.",
    )
    return notice + yaml.safe_dump(content, allow_unicode=True, sort_keys=False, width=110)


def generated_files(project: Project | None = None) -> dict[Path, str]:
    project = project or current()
    return {u.folder / RELATIVE_PATH: generate(project, u) for u in project.products() if u.internal}


def sync(project: Project | None = None) -> list[Path]:
    written = []
    for path, text in generated_files(project).items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


def outdated(project: Project | None = None) -> list[Path]:
    return [c for c, t in generated_files(project).items() if not c.exists() or c.read_text(encoding="utf-8") != t]
